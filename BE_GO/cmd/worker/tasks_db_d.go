package main

import (
	"log/slog"
	"os"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/redisclient"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/stripeapi"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

// tasksDBDLongTimeout bounds change_type_of_stripe_payments, which waits one
// second after every Stripe call as Django's task does (Celery sets no limit).
const tasksDBDLongTimeout = 2 * time.Hour

// composeTasksDBD builds the Stripe and membership tasks with the Stripe
// settings Django's worker reads (instance override, else STRIPE_*) and the
// Django cache (Redis DB 1) for the renewal lock.
func composeTasksDBD(cfg config.Config, db *sqlx.DB, tasks celery.Publisher, cache *redis.Client, logger *slog.Logger) *service.TasksDBD {
	client := stripeapi.NewClient(os.Getenv("STRIPE_API_BASE"))
	stripe := service.NewStripeRoutes(repository.NewStripeRoutes(db), repository.NewSocialConfiguration(db),
		service.StripeEnvironmentKeys{PublicKey: cfg.InstanceAdmin.StripePublicKey, SecretKey: cfg.InstanceAdmin.StripeKey,
			WebhookSecret: cfg.InstanceAdmin.StripeWebhookSecret}, cfg.SessionSecret, client, time.Now)
	return service.NewTasksDBD(repository.NewTasksDBD(db), stripe, client, tasks, repository.NewTasksDBDRenewalLock(cache), time.Now, logger)
}

// tasksDBDCache is the Django cache client: the broker's Redis options on DB 1.
func tasksDBDCache(cfg config.Config) (*redis.Client, error) {
	broker, err := celeryBroker(cfg)
	if err != nil {
		return nil, err
	}
	options := *broker.Options()
	_ = broker.Close()
	options.DB = 1
	options.Dialer = redisclient.DialContext(&options)
	return redis.NewClient(&options), nil
}
