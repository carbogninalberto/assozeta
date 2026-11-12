package domain

import (
	"context"
	"time"
)

// SubscriptionSignatureLegacyRow is one (subscription_id, signature) pair of
// the legacy application_subscription.signature column.
type SubscriptionSignatureLegacyRow struct {
	ID        string `db:"subscription_id"`
	Signature string `db:"signature"`
}

// SubscriptionSignatureMigrationRow is Subscription.objects.get(subscription_id=...)
// with what Subscription.save() reads before writing: the creation date and
// the association owner's balance-year settings.
type SubscriptionSignatureMigrationRow struct {
	ID           string    `db:"subscription_id"`
	Association  string    `db:"sport_association_id"`
	Key          *string   `db:"signature_storage_key"`
	URL          *string   `db:"signature_url"`
	CreationDate time.Time `db:"creation_date"`
	StartDay     int       `db:"subscription_start_day"`
	StartMonth   int       `db:"subscription_start_month"`
	CustomEnd    bool      `db:"custom_end_date"`
	EndDay       int       `db:"subscription_end_day"`
	EndMonth     int       `db:"subscription_end_month"`
}

// SubscriptionSignatureMigrationStore reads the legacy column and saves the
// destination fields (save(update_fields=['signature_url', 'signature_storage_key'])).
type SubscriptionSignatureMigrationStore interface {
	LegacyColumnExists(ctx context.Context) (bool, error)
	LegacyCount(ctx context.Context) (int64, error)
	LegacyRows(ctx context.Context, limit, offset int64) ([]SubscriptionSignatureLegacyRow, error)
	// Subscription is nil when no row has the id (DoesNotExist).
	Subscription(ctx context.Context, id string) (*SubscriptionSignatureMigrationRow, error)
	// Save writes the update entry (and its index row) and the two columns.
	// Atomic is transaction.atomic(); otherwise each statement autocommits
	// (the entry is written by pre_save, before the UPDATE).
	Save(ctx context.Context, row SubscriptionSignatureMigrationRow, key, url *string, at time.Time, atomic bool) error
}
