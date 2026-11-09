package main

import (
	"log/slog"
	"os"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/channels"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/stripeapi"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

// composeStripePaidCheck builds mark_as_paid_payments_checking_stripe: the
// Stripe routes' settings and client with the paid flow (invoice, the
// print_document_invoice publication, DOCUMENT_BYPASS_TOKEN) and the
// notifications Django's NotificationService writes: the Redis DB 0 store,
// the user_broadcasts entry in the Django cache (DB 1) and the Channels push.
func composeStripePaidCheck(cfg config.Config, db *sqlx.DB, tasks celery.Publisher, store, cache *redis.Client,
	logger *slog.Logger) (*service.StripePaidCheck, error) {
	notifications, err := repository.NewNotificationStore(db, store, cache)
	if err != nil {
		return nil, err
	}
	notifier := service.NewSubscriptionNotifier(repository.NewSubscriptionNotifications(db), notifications,
		channels.NewLayer(store, nil, nil, nil), time.Now)
	stripe := service.NewStripeRoutes(repository.NewStripeRoutes(db), repository.NewSocialConfiguration(db),
		service.StripeEnvironmentKeys{PublicKey: cfg.InstanceAdmin.StripePublicKey, SecretKey: cfg.InstanceAdmin.StripeKey,
			WebhookSecret: cfg.InstanceAdmin.StripeWebhookSecret}, cfg.SessionSecret, stripeapi.NewClient(os.Getenv("STRIPE_API_BASE")), time.Now).
		WithPaidFlow(repository.NewPaymentPaidFlows(repository.NewPaymentStates(db)), notifier, tasks, os.Getenv("DOCUMENT_BYPASS_TOKEN"))
	return service.NewStripePaidCheck(repository.NewStripePaidChecks(db), stripe, logger), nil
}
