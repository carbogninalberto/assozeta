package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type SubscriptionImport struct{ db *sqlx.DB }

func NewSubscriptionImport(db *sqlx.DB) *SubscriptionImport { return &SubscriptionImport{db: db} }

var _ domain.SubscriptionImportStore = (*SubscriptionImport)(nil)

func (r *SubscriptionImport) BeginSubscriptionImport(ctx context.Context) (domain.SubscriptionImportTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted, ReadOnly: true})
	if err != nil {
		return nil, err
	}
	return &subscriptionImportTransaction{ownedTransaction: tx}, nil
}

type subscriptionImportTransaction struct{ *ownedTransaction }

func (t *subscriptionImportTransaction) Association(ctx context.Context, user string, impersonation *string) (*string, error) {
	var id string
	err := t.tx.GetContext(ctx, &id, `SELECT sport_association_id FROM application_sportassociation WHERE user_id=$1 AND NOT deleted
 AND ($2::uuid IS NULL OR sport_association_id=$2::uuid) ORDER BY sport_association_id LIMIT 1`, user, impersonation)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &id, err
}

func (t *subscriptionImportTransaction) DocumentExists(ctx context.Context, id string) (bool, error) {
	var found bool
	err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM docmanager_document WHERE document_id=$1)`, id)
	return found, err
}
