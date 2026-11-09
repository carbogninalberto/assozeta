package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// AuditIndexBackfill is the storage of the backfill_audit_log_index command.
type AuditIndexBackfill struct{ db *sqlx.DB }

func NewAuditIndexBackfill(db *sqlx.DB) *AuditIndexBackfill { return &AuditIndexBackfill{db: db} }

// AuditIndexPendingEntry is one LogEntry without an index row and its resolution.
type AuditIndexPendingEntry struct {
	ID         int64
	ContentKey string
	Resolution *AuditLogIndexResolution
}

// ContentTypeID is ContentType.objects.get(app_label=..., model=...); ok
// false is DoesNotExist.
func (r *AuditIndexBackfill) ContentTypeID(ctx context.Context, appLabel, model string) (int64, bool, error) {
	var id int64
	err := r.db.GetContext(ctx, &id, `SELECT id FROM django_content_type WHERE app_label=$1 AND model=$2`, appLabel, model)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, false, nil
	}
	return id, err == nil, err
}

const auditIndexPendingSQL = ` FROM auditlog_logentry e JOIN django_content_type ct ON ct.id=e.content_type_id
 WHERE NOT (e.id IN (SELECT log_entry_id FROM application_auditlogindex)) AND ($1::integer IS NULL OR e.content_type_id=$1)`

// CountPending is the pending queryset's count().
func (r *AuditIndexBackfill) CountPending(ctx context.Context, contentType *int64) (int64, error) {
	var count int64
	err := r.db.GetContext(ctx, &count, `SELECT COUNT(*)`+auditIndexPendingSQL, contentType)
	return count, err
}

// PendingBatch evaluates the pending queryset (ordered by pk) at the given
// offset and size and resolves each entry without request context.
func (r *AuditIndexBackfill) PendingBatch(ctx context.Context, contentType *int64, offset, size int64) ([]AuditIndexPendingEntry, error) {
	type row struct {
		ID int64 `db:"id"`
		auditIndexEntry
	}
	var rows []row
	err := r.db.SelectContext(ctx, &rows, `SELECT e.id, ct.app_label, ct.model, e.object_pk, e.changes::text AS changes,
 e.actor_id::text AS actor_id`+auditIndexPendingSQL+` ORDER BY e.id LIMIT $2 OFFSET $3`, contentType, size, offset)
	if err != nil {
		return nil, err
	}
	entries := make([]AuditIndexPendingEntry, 0, len(rows))
	for _, item := range rows {
		resolution, err := resolveAuditIndexEntry(ctx, r.db, item.auditIndexEntry, "")
		if err != nil {
			return nil, err
		}
		entries = append(entries, AuditIndexPendingEntry{ID: item.ID, ContentKey: item.AppLabel + "." + item.Model, Resolution: resolution})
	}
	return entries, nil
}

// AuditIndexRow is one AuditLogIndex to create.
type AuditIndexRow struct {
	EntryID int64
	*AuditLogIndexResolution
	CreatedAt time.Time
}

// CreateIndexes is bulk_create(ignore_conflicts=True) inside transaction.atomic().
func (r *AuditIndexBackfill) CreateIndexes(ctx context.Context, rows []AuditIndexRow) error {
	tx, err := r.db.BeginTxx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	for _, item := range rows {
		if _, err = tx.ExecContext(ctx, `INSERT INTO application_auditlogindex
 (audit_log_index_id,log_entry_id,sport_association_id,resolution_path,created_at) VALUES($1,$2,$3,$4,$5)
 ON CONFLICT DO NOTHING`, uuid.NewString(), item.EntryID, item.AssociationID, item.Path, item.CreatedAt); err != nil {
			return err
		}
	}
	return tx.Commit()
}
