// This explicit process visits persisted schedules in the supported subset. It
// neither migrates/seeds definitions nor changes ownership or installed services.
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
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/migrations"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		slog.Error(err.Error())
		os.Exit(1)
	}
}

func run(arguments []string) error {
	flags := flag.NewFlagSet("scheduler", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	envFile := flags.String("env-file", "", "optional existing deployment .env")
	pageSize := flags.Int("page-size", 16, "maximum sources per page, from 1 to 64")
	poll := flags.Duration("poll-interval", time.Second, "positive delay between pages, at most 5s")
	tick := flags.Duration("tick-timeout", 2*time.Second, "page work deadline, at most 5s")
	source := flags.Duration("source-timeout", 500*time.Millisecond, "source work deadline, at most the page deadline")
	startup := flags.Duration("startup-timeout", 5*time.Second, "startup database deadline, at most 30s")
	if err := flags.Parse(arguments); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			fmt.Println("Go scheduler: --env-file PATH; bounded --page-size, --poll-interval, --tick-timeout, --source-timeout and --startup-timeout. Requires existing migrations, Beat source tables and restore lock path. Initialization does not imply Go ownership; no schedules or ownership are seeded. Cancellation joins admitted SQL cleanup, which can outlast work deadlines.")
			return nil
		}
		return errors.New("invalid scheduler arguments")
	}
	if flags.NArg() != 0 {
		return errors.New("unexpected scheduler arguments")
	}
	if *pageSize < 1 || *pageSize > 64 || *poll < time.Millisecond || *poll > 5*time.Second ||
		*tick < time.Millisecond || *tick > 5*time.Second || *source < time.Millisecond || *source > *tick ||
		*startup < time.Millisecond || *startup > 30*time.Second {
		return errors.New("invalid scheduler execution bounds")
	}
	cfg, err := config.Load(*envFile)
	if err != nil {
		return errors.New("invalid scheduler environment configuration")
	}
	if strings.TrimSpace(cfg.RestoreLockPath) == "" {
		return errors.New("scheduler requires the existing restore coordination lock path")
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	locks := repository.NewRestoreFileLock(cfg.RestoreLockPath)
	lease, err := locks.Acquire(ctx, false, true)
	if err != nil && !errors.Is(err, domain.ErrRestoreBusy) {
		return errors.New("scheduler restore coordination unavailable")
	}
	if lease != nil {
		if err = lease.Close(); err != nil {
			return errors.New("scheduler restore coordination unavailable")
		}
	}
	// A held maintenance lease is temporary, but it must not hide missing schema.
	// Startup performs only bounded reads; per-source publication reacquires the
	// same file lease and live restore/ownership admission independently.
	database, err := pgx.ParseConfig(cfg.DatabaseURL())
	if err != nil {
		return errors.New("invalid scheduler database configuration")
	}
	database.DefaultQueryExecMode = pgx.QueryExecModeExec
	database.ConnectTimeout = 5 * time.Second
	database.RuntimeParams["application_name"] = "assozeta-go-scheduler"
	db := sqlx.NewDb(stdlib.OpenDB(*database), "pgx")
	defer db.Close()
	// Pages are serial. An occurrence owns one connection; discovery and restore
	// reads precede it. No independent heartbeat or unbounded pool is needed.
	db.SetMaxOpenConns(2)
	db.SetMaxIdleConns(1)
	db.SetConnMaxLifetime(30 * time.Minute)
	candidates := repository.NewScheduleCandidates(db)
	publisher := service.NewOccurrencePublisher(repository.NewOccurrences(db), locks, repository.NewRestoreStates(db))
	pager, err := service.NewSchedulePager(candidates, publisher, service.SchedulePageOptions{
		Limit: *pageSize, TickTimeout: *tick, SourceTimeout: *source})
	if err != nil {
		return errors.New("invalid scheduler execution bounds")
	}
	logger := slog.Default()
	loop, err := service.NewScheduleLoop(&diagnosticTicker{next: pager, logger: logger}, service.ScheduleLoopOptions{PollInterval: *poll})
	if err != nil {
		return errors.New("invalid scheduler execution bounds")
	}
	startupCtx, cancelStartup := context.WithTimeout(ctx, *startup)
	defer cancelStartup()
	if err = db.PingContext(startupCtx); err != nil {
		return errors.New("scheduler database unavailable")
	}
	if err = repository.NewJobs(db).CheckSchema(startupCtx, migrations.CurrentVersion); err != nil {
		return errors.New("scheduler operational schema unavailable or incompatible")
	}
	if err = candidates.CheckSchema(startupCtx); err != nil {
		return errors.New("scheduler periodic source schema unavailable or incompatible")
	}
	cancelStartup()
	logger.Info("scheduler process initialized", "page_size", *pageSize)
	_, err = loop.Run(ctx, domain.ScheduleCursor{})
	if err != nil && !(errors.Is(err, context.Canceled) && ctx.Err() != nil) {
		return errors.New("scheduler loop failed")
	}
	logger.Info("scheduler stopped")
	return nil
}

// Status is observational, not an ownership heartbeat. The pager may visit only
// disabled/unrelated rows without reaching admission. Repeated identical states
// are coalesced; source names, payloads and underlying SQL errors are never logged.
type diagnosticTicker struct {
	next   domain.ScheduleTicker
	logger *slog.Logger
	last   string
}

func (t *diagnosticTicker) Tick(ctx context.Context, cursor domain.ScheduleCursor) (domain.SchedulePageResult, error) {
	result, err := t.next.Tick(ctx, cursor)
	if ctx.Err() != nil {
		return result, err
	}
	state, level := "scheduler page completed", slog.LevelInfo
	switch {
	case errors.Is(err, domain.ErrOccurrenceInactive):
		state = "scheduler inactive"
	case errors.Is(err, domain.ErrRestoreBusy), errors.Is(err, domain.ErrRestorePending):
		state = "scheduler deferred"
	case err != nil:
		state, level = "scheduler degraded", slog.LevelWarn
	default:
		for _, outcome := range result.Outcomes {
			if outcome.Status == domain.SchedulePageUnsupported {
				state, level = "scheduler unsupported definitions", slog.LevelWarn
				break
			}
		}
	}
	if state != t.last {
		t.logger.Log(ctx, level, state)
		t.last = state
	}
	return result, err
}
