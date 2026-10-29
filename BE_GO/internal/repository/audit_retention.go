package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jmoiron/sqlx"
)

type AuditRetentions struct{ db *sqlx.DB }

func NewAuditRetentions(db *sqlx.DB) *AuditRetentions { return &AuditRetentions{db: db} }
func (r *AuditRetentions) BeginAuditRetention(ctx context.Context, job domain.Job) (domain.AuditRetentionTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	// Lock ownership and check the live lease before deletion can start.
	// The job row remains available to heartbeats; completion rechecks the lease.
	if err = fenceJobEffects(ctx, tx.tx, job.ID, job.LeaseToken); err != nil {
		_ = tx.Rollback()
		return nil, err
	}
	return &auditRetentionTransaction{ownedTransaction: tx}, nil
}

type auditRetentionTransaction struct{ *ownedTransaction }

// DeleteExpiredAuditRecords replaces the collector's explicit index cascade.
// Django's existing foreign key is deferred; both deletes and fenced completion
// share the transaction. The query returns only the aggregate collector count.
func (t *auditRetentionTransaction) DeleteExpiredAuditRecords(ctx context.Context, cutoff time.Time) (int64, error) {
	var count int64
	err := t.tx.GetContext(ctx, &count, deleteExpiredAuditRecordsSQL, cutoff)
	return count, err
}

const deleteExpiredAuditRecordsSQL = `WITH expired_audits AS (
 DELETE FROM auditlog_logentry WHERE timestamp<$1 RETURNING id
), expired_indexes AS (
 DELETE FROM application_auditlogindex i USING expired_audits a
 WHERE i.log_entry_id=a.id RETURNING i.audit_log_index_id
)
SELECT (SELECT count(*) FROM expired_audits)+(SELECT count(*) FROM expired_indexes)`

func (t *auditRetentionTransaction) CompleteJob(ctx context.Context, id, token uuid.UUID) error {
	return completeJob(ctx, t.tx, id, token, nil)
}
func (t *auditRetentionTransaction) Commit() error {
	err := t.ownedTransaction.Commit()
	var constraint *pgconn.PgError
	// A PostgreSQL FK rejection at COMMIT has definitely rolled back this
	// transaction. Never classify transport errors or other commit failures as
	// this handled business conflict: their outcome may be unknown.
	if errors.As(err, &constraint) && constraint.Code == "23503" {
		return domain.ErrAuditRetentionConflict
	}
	return err
}
func (t *auditRetentionTransaction) Rollback() error { return t.ownedTransaction.Rollback() }
