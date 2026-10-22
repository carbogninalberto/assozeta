package repository

import (
	"context"
	"database/sql"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

type EmailBalanceResets struct{ db *sqlx.DB }

func NewEmailBalanceResets(db *sqlx.DB) *EmailBalanceResets { return &EmailBalanceResets{db: db} }

func (r *EmailBalanceResets) BeginEmailBalanceReset(ctx context.Context, job domain.Job) (domain.EmailBalanceResetTransaction, error) {
	tx, err := r.db.BeginTxx(ctx, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	if err = fenceJobEffects(ctx, tx, job.ID, job.LeaseToken); err != nil {
		_ = tx.Rollback()
		return nil, err
	}
	return &emailBalanceResetTransaction{tx: tx}, nil
}

type emailBalanceResetTransaction struct{ tx *sqlx.Tx }

func (t *emailBalanceResetTransaction) ResetPositiveCounters(ctx context.Context) (int64, error) {
	// Django uses the unfiltered manager, including deleted associations and NULL
	// association references. Bulk UPDATE intentionally bypasses save/audit hooks.
	result, err := t.tx.ExecContext(ctx, `UPDATE communications_communicationconfiguration SET daily_email_balance=0 WHERE daily_email_balance>0`)
	if err != nil {
		return 0, err
	}
	return result.RowsAffected()
}
func (t *emailBalanceResetTransaction) CompleteJob(ctx context.Context, id, token uuid.UUID) error {
	return completeJob(ctx, t.tx, id, token, nil)
}
func (t *emailBalanceResetTransaction) Commit() error   { return t.tx.Commit() }
func (t *emailBalanceResetTransaction) Rollback() error { return t.tx.Rollback() }
