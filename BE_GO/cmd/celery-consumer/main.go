// The Celery consumer executes Celery task messages from the dedicated Go
// queue (assozeta-go) that core.task_routing routes the GO_TASK_QUEUE_TASKS
// names to. It is not started by default: Django's worker keeps consuming
// the "celery" queue, and with GO_TASK_QUEUE_TASKS empty nothing is routed
// here. Messages for tasks without a Go implementation, or inputs outside a
// task's ported subset, are handed off unchanged to the "celery" queue.
// See docs/architecture/go-migration/celery-consumer.md.
package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"flag"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/redisclient"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/smtpsend"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		slog.Error(err.Error())
		os.Exit(1)
	}
}

func run(arguments []string) error {
	flags := flag.NewFlagSet("celery-consumer", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	envFile := flags.String("env-file", "", "optional existing deployment .env")
	concurrency := flags.Int("concurrency", 2, "execution slots, from 1 to 32")
	drain := flags.Duration("drain-timeout", 30*time.Second, "grace for running tasks at shutdown")
	// Internal: one prefork job (see celeryworker/prefork.go), started by the consumer itself.
	child := flags.Bool("prefork-child", false, "internal")
	if err := flags.Parse(arguments); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			fmt.Println("Go Celery consumer: --env-file PATH, --concurrency N, --drain-timeout D. Consumes the assozeta-go queue " +
				"(GO_TASK_QUEUE_TASKS routing); runs core.tasks.send_mail_async, send_email_text, send_email_template, send_email_html, the automation workflow tasks, the print tasks, save_to_storage and export_invoices_to_zip and hands every other message to the celery queue.")
			return nil
		}
		return errors.New("invalid celery consumer arguments")
	}
	if flags.NArg() != 0 {
		return errors.New("unexpected celery consumer arguments")
	}
	cfg, err := config.Load(*envFile)
	if err != nil {
		return errors.New("invalid celery consumer environment configuration")
	}
	database, err := pgx.ParseConfig(cfg.DatabaseURL())
	if err != nil {
		return errors.New("invalid celery consumer database configuration")
	}
	database.DefaultQueryExecMode = pgx.QueryExecModeExec
	database.ConnectTimeout = 5 * time.Second
	database.RuntimeParams["application_name"] = "assozeta-go-celery-consumer"
	db := sqlx.NewDb(ownedpgx.OpenDB(*database), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(*concurrency + 2)
	broker, err := celeryBroker(cfg)
	if err != nil {
		return err
	}
	defer broker.Close()
	admin := cfg.InstanceAdmin
	mail := service.NewSendMailAsync(repository.NewSendMailAsyncStore(db), service.SystemEmail{Host: admin.EmailHost,
		Username: admin.EmailUsername, Password: admin.EmailPassword, Port: admin.EmailPort, PortInvalid: admin.EmailPortInvalid,
		UseTLS: admin.EmailUseTLS, UseSSL: admin.EmailUseSSL, LocalHostname: dnsName()}, time.Now)
	// Messages the e-mail tasks publish (send_mail_async) follow GO_TASK_QUEUE_TASKS like Django's producer.
	emailPublisher := celery.NewProducer(celery.RedisBroker{Client: broker}).WithRoutes(celery.ParseRoutes(cfg.GoTaskQueueTasks))
	mail.WithEmailTasks(emailPublisher, service.ConsumerEmailSettings{AppURL: admin.AppURL, WhitelabelName: cfg.WhitelabelName,
		DefaultFromEmail: cfg.DefaultFromEmail, DefaultTeamEmail: cfg.DefaultTeamEmail, IsWhitelabel: cfg.IsWhitelabel,
		SecretKeys: []string{cfg.SessionSecret}}, slog.Default())
	host, _ := os.Hostname()
	if host == "" {
		host = "localhost"
	}
	transport := &celery.Transport{Client: broker, Queue: celery.GoQueue}
	admission := service.NewJobRestoreAdmission(repository.NewRestoreFileLock(cfg.RestoreLockPath),
		repository.NewRestoreStates(db), cfg.RestoreLockPath != "")
	tasks := map[string]celeryworker.Task{service.SendMailAsyncTask: mail.Task(),
		service.SendEmailTextTask: mail.SendEmailTextTask(), service.SendEmailTemplateTask: mail.SendEmailTemplateTask(),
		service.SendEmailHTMLTask: mail.SendEmailHTMLTask()}
	// Messages the tasks publish (send_task inside a worker task) use the
	// same routes as every other producer.
	producer := celery.NewProducer(celery.RedisBroker{Client: broker}).WithRoutes(celery.ParseRoutes(cfg.GoTaskQueueTasks))
	for name, task := range service.NewWorkflowTasks(repository.NewWorkflowTaskStore(db), producer, time.Now).Tasks() {
		tasks[name] = task
	}
	if err = consumerPrintRegister(tasks, cfg, db, *envFile); err != nil {
		return err
	}
	for _, register := range extraTasks {
		register(tasks)
	}
	childArgs := []string{"--prefork-child"}
	if *envFile != "" {
		childArgs = append(childArgs, "--env-file", *envFile)
	}
	worker, err := celeryworker.New(transport, tasks,
		repository.NewCeleryResults(db), admission, celeryworker.Options{Concurrency: *concurrency, HandoffQueue: celery.DefaultQueue,
			Origin: "gen" + strconv.Itoa(os.Getpid()) + "@" + host, Hostname: "celery@" + host, DrainTimeout: *drain,
			Coordinated: strings.TrimSpace(cfg.RestoreLockPath) != "", Child: celeryworker.ChildCommand(childArgs...)}, slog.Default())
	if err != nil {
		return errors.New("invalid celery consumer bounds")
	}
	if *child {
		events := os.NewFile(3, "prefork-events")
		if events == nil {
			return errors.New("prefork child without an event pipe")
		}
		defer events.Close()
		if err := worker.RunChild(context.Background(), os.Stdin, events); err != nil {
			return errors.New("prefork child failed")
		}
		mail.Wait()
		return nil
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	startup, cancel := context.WithTimeout(ctx, 5*time.Second)
	err = db.PingContext(startup)
	if err == nil {
		err = broker.Ping(startup).Err()
	}
	cancel()
	if err != nil {
		return errors.New("celery consumer database or broker unavailable")
	}
	err = worker.Run(ctx)
	// The delayed send_email_html sends run to completion, as the worker
	// process waits for the tasks' non-daemon Timer threads.
	mail.Wait()
	if err != nil {
		return errors.New("celery consumer stopped with an error")
	}
	slog.Info("celery consumer stopped")
	return nil
}

// extraTasks registers tasks of optional builds (the consumerprobe tag).
var extraTasks []func(map[string]celeryworker.Task)

// dnsName is django.core.mail.utils.DNS_NAME (socket.getfqdn()).
func dnsName() string {
	host, _ := os.Hostname()
	return smtpsend.FQDN(host)
}

// celeryBroker is the Redis client for CELERY_BROKER_URL (DB 0), as in cmd/worker.
func celeryBroker(cfg config.Config) (*redis.Client, error) {
	options := &redis.Options{Addr: cfg.RedisAddress, Username: cfg.RedisUsername, Password: cfg.RedisPassword,
		DB: 0, MaxRetries: -1, DialTimeout: 2 * time.Second, ReadTimeout: 2 * time.Second, WriteTimeout: 2 * time.Second,
		ContextTimeoutEnabled: true}
	if cfg.RedisTLS {
		options.TLSConfig = &tls.Config{MinVersion: tls.VersionTLS12}
		if cfg.RedisRootCertificate != "" {
			pem, err := os.ReadFile(cfg.RedisRootCertificate)
			if err != nil {
				return nil, errors.New("cannot read Redis root certificate")
			}
			roots, err := x509.SystemCertPool()
			if err != nil {
				roots = x509.NewCertPool()
			}
			if !roots.AppendCertsFromPEM(pem) {
				return nil, errors.New("invalid Redis root certificate")
			}
			options.TLSConfig.RootCAs = roots
		}
	}
	options.Dialer = redisclient.DialContext(options)
	return redis.NewClient(options), nil
}
