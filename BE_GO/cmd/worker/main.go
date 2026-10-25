// The worker consumes only explicitly registered Go jobs. It neither migrates
// the database nor publishes jobs, changes runtime ownership, or starts a scheduler.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/migrations"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		slog.Error(err.Error())
		os.Exit(1)
	}
}

func run(arguments []string) error {
	flags := flag.NewFlagSet("worker", flag.ContinueOnError)
	// Parser errors can contain supplied arguments; emit only fixed diagnostics.
	flags.SetOutput(io.Discard)
	envFile := flags.String("env-file", "", "optional existing deployment .env")
	concurrency := flags.Int("concurrency", 2, "execution slots, from 1 to 32")
	lease := flags.Duration("lease", 30*time.Second, "durable claim lease")
	renew := flags.Duration("renew-interval", 10*time.Second, "lease renewal interval")
	storeTimeout := flags.Duration("store-timeout", 5*time.Second, "bounded queue operation deadline")
	poll := flags.Duration("poll-interval", time.Second, "idle claim interval")
	maintenance := flags.Duration("maintenance-interval", 15*time.Second, "restore deferral interval")
	drain := flags.Duration("drain-timeout", 25*time.Second, "grace before cancelling admitted jobs")
	taskTimeout := flags.Duration("task-timeout", time.Minute, "registered email reset, associate age and token cleanup task deadline")
	if err := flags.Parse(arguments); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			fmt.Println("Go worker: --env-file PATH; registered tasks: daily email reset, associate age promotion and expired subscription token cleanup. Bounded --concurrency, --lease, --renew-interval, --store-timeout, --poll-interval, --maintenance-interval, --drain-timeout and --task-timeout. Requires prior explicit migration and coordinated ownership; does not activate Go ownership or schedule jobs.")
			return nil
		}
		return errors.New("invalid worker arguments")
	}
	if flags.NArg() != 0 {
		return errors.New("unexpected worker arguments")
	}
	cfg, err := config.Load(*envFile)
	if err != nil {
		return errors.New("invalid worker environment configuration")
	}
	// Native --env-file invocation does not inherit Compose's environment
	// overrides. Never turn a missing deployment lease into uncoordinated work.
	if strings.TrimSpace(cfg.RestoreLockPath) == "" {
		return errors.New("worker requires the existing restore coordination lock path")
	}
	database, err := pgx.ParseConfig(cfg.DatabaseURL())
	if err != nil {
		return errors.New("invalid worker database configuration")
	}
	// Keep transaction-pooler compatibility and identify this native consumer for
	// operations without exposing database credentials or job payloads in logs.
	database.DefaultQueryExecMode = pgx.QueryExecModeExec
	database.ConnectTimeout = 5 * time.Second
	database.RuntimeParams["application_name"] = "assozeta-go-worker"
	db := sqlx.NewDb(ownedpgx.OpenDB(*database), "pgx")
	defer db.Close()
	logger := slog.Default()
	reset := service.NewEmailBalanceReset(repository.NewEmailBalanceResets(db), logger)
	ages := service.NewAssociateAgeUpdate(repository.NewAssociateAgeUpdates(db), time.Now, logger)
	tokens := service.NewSubscriptionTokenCleanup(repository.NewSubscriptionTokenCleanups(db), time.Now, logger)
	admission := service.NewJobRestoreAdmission(repository.NewRestoreFileLock(cfg.RestoreLockPath),
		repository.NewRestoreStates(db), cfg.RestoreLockPath != "")
	jobs := repository.NewJobs(db)
	executor, err := service.NewJobExecutor(jobs, admission,
		map[string]service.JobDefinition{
			domain.JobResetDailyEmailBalance:         {Version: 1, Timeout: *taskTimeout, Run: reset.Run},
			domain.JobUpdateAssociateAge:             {Version: 1, Timeout: *taskTimeout, Run: ages.Run},
			domain.JobClearExpiredSubscriptionTokens: {Version: 1, Timeout: *taskTimeout, Run: tokens.Run},
		},
		service.JobExecutorOptions{Owner: "worker-" + uuid.NewString(), Concurrency: *concurrency,
			Lease: *lease, RenewInterval: *renew, StoreTimeout: *storeTimeout, PollInterval: *poll,
			MaintenanceInterval: *maintenance, DrainTimeout: *drain})
	if err != nil {
		return errors.New("invalid worker execution bounds")
	}
	// Each slot may need an effect transaction and an independent heartbeat.
	// Bounds are validated above, before any connection or mutation is attempted.
	db.SetMaxOpenConns(2*(*concurrency) + 2)
	db.SetMaxIdleConns(*concurrency + 1)
	db.SetConnMaxLifetime(30 * time.Minute)
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	startup, cancelStartup := context.WithTimeout(ctx, *storeTimeout)
	err = db.PingContext(startup)
	if err != nil {
		cancelStartup()
		return errors.New("worker database unavailable")
	}
	err = jobs.CheckSchema(startup, migrations.CurrentVersion)
	cancelStartup()
	if err != nil {
		return errors.New("worker operational schema unavailable or incompatible")
	}
	logger.Info("worker ready", "concurrency", *concurrency)
	if err = executor.Run(ctx); err != nil {
		if errors.Is(err, domain.ErrJobDrainTimeout) {
			return errors.New("worker drain deadline exceeded")
		}
		return errors.New("worker execution failed")
	}
	logger.Info("worker stopped")
	return nil
}
