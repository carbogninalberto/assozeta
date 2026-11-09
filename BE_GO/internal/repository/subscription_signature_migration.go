package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// SubscriptionSignatureMigrations persists migrate_subscription_signatures_to_s3
// (subscription-signature-migration.md).
type SubscriptionSignatureMigrations struct{ db *sqlx.DB }

func NewSubscriptionSignatureMigrations(db *sqlx.DB) *SubscriptionSignatureMigrations {
	return &SubscriptionSignatureMigrations{db: db}
}

var _ domain.SubscriptionSignatureMigrationStore = (*SubscriptionSignatureMigrations)(nil)

// LegacyColumnExists is connection.introspection.get_table_description: the
// columns of the table visible on the search path.
func (r *SubscriptionSignatureMigrations) LegacyColumnExists(ctx context.Context) (bool, error) {
	var exists bool
	err := r.db.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a
 JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
 WHERE c.relname='application_subscription' AND pg_catalog.pg_table_is_visible(c.oid)
 AND a.attname='signature' AND a.attnum>0 AND NOT a.attisdropped)`)
	return exists, err
}

func (r *SubscriptionSignatureMigrations) LegacyCount(ctx context.Context) (int64, error) {
	var count int64
	err := r.db.GetContext(ctx, &count, `SELECT COUNT(*) FROM "application_subscription" WHERE "signature" IS NOT NULL AND "signature" <> $1`, "")
	return count, err
}

func (r *SubscriptionSignatureMigrations) LegacyRows(ctx context.Context, limit, offset int64) ([]domain.SubscriptionSignatureLegacyRow, error) {
	var rows []domain.SubscriptionSignatureLegacyRow
	err := r.db.SelectContext(ctx, &rows, `SELECT "subscription_id"::text AS subscription_id, "signature"::text AS signature
 FROM "application_subscription" WHERE "signature" IS NOT NULL AND "signature" <> $1
 ORDER BY "subscription_id" LIMIT $2 OFFSET $3`, "", limit, offset)
	return rows, err
}

// Subscription is the default manager without a request group: every row by id.
func (r *SubscriptionSignatureMigrations) Subscription(ctx context.Context, id string) (*domain.SubscriptionSignatureMigrationRow, error) {
	var row domain.SubscriptionSignatureMigrationRow
	err := r.db.GetContext(ctx, &row, `SELECT s.subscription_id::text, s.sport_association_id::text, s.signature_storage_key,
 s.signature_url, s.creation_date, u.subscription_start_day, u.subscription_start_month, u.custom_end_date,
 u.subscription_end_day, u.subscription_end_month
 FROM application_subscription s
 JOIN application_sportassociation a ON a.sport_association_id=s.sport_association_id
 JOIN bakney_user u ON u.user_id=a.user_id
 WHERE s.subscription_id=$1::uuid`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func subscriptionSignatureMigrationChanges(row domain.SubscriptionSignatureMigrationRow, key, url *string) map[string][2]string {
	updated := map[string][2]string{}
	if subscriptionSignText(row.URL) != subscriptionSignText(url) {
		updated["signature_url"] = [2]string{subscriptionSignText(row.URL), subscriptionSignText(url)}
	}
	if subscriptionSignText(row.Key) != subscriptionSignText(key) {
		updated["signature_storage_key"] = [2]string{subscriptionSignText(row.Key), subscriptionSignText(key)}
	}
	return updated
}

func (r *SubscriptionSignatureMigrations) Save(ctx context.Context, row domain.SubscriptionSignatureMigrationRow, key, url *string,
	at time.Time, atomic bool) error {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return err
	}
	defer tx.Rollback()
	// pre_save: auditlog's update entry (no actor, no request), indexed direct.
	if updated := subscriptionSignatureMigrationChanges(row, key, url); len(updated) > 0 {
		direct := "direct"
		if err := appendModelAudit(ctx, tx.tx, modelAuditEntry{Model: "subscription", Key: row.ID,
			Representation: "Subscription object (" + row.ID + ")", Action: 1, Changes: updated,
			AssociationID: &row.Association, ResolutionPath: &direct, At: at}); err != nil {
			return err
		}
	}
	const update = `UPDATE application_subscription SET signature_url=$2, signature_storage_key=$3 WHERE subscription_id=$1::uuid`
	if atomic {
		if _, err := tx.tx.ExecContext(ctx, update, row.ID, url, key); err != nil {
			return err
		}
		return tx.Commit()
	}
	// Autocommit: the entry stays when the UPDATE then fails.
	if err := tx.Commit(); err != nil {
		return err
	}
	_, err = r.db.ExecContext(ctx, update, row.ID, url, key)
	return err
}
