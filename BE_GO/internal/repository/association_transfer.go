package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

// AssociationTransfer reads the association export/import state: archives,
// task results, the instance configuration and Django cache keys (DB 1).
type AssociationTransfer struct {
	db    *sqlx.DB
	cache *redis.Client
}

func NewAssociationTransfer(db *sqlx.DB, cache *redis.Client) *AssociationTransfer {
	return &AssociationTransfer{db: db, cache: cache}
}

var _ domain.AssociationTransferStore = (*AssociationTransfer)(nil)

func (r *AssociationTransfer) OwnedAssociation(ctx context.Context, user string) (string, bool, error) {
	var id string
	err := r.db.GetContext(ctx, &id, `SELECT sport_association_id::text FROM application_sportassociation WHERE user_id=$1::uuid AND NOT deleted`, user)
	if errors.Is(err, sql.ErrNoRows) {
		return "", false, nil
	}
	return id, err == nil, err
}

const associationExportJoin = ` FROM application_sportassociationdocumentsarchive a
  JOIN docmanager_document d ON d.document_id=a.document_id
 WHERE a.sport_association_id=$1::uuid AND d.filename LIKE 'export\_%'`

func (r *AssociationTransfer) ExportCount(ctx context.Context, association string) (int, error) {
	var count int
	err := r.db.GetContext(ctx, &count, `SELECT COUNT(*)`+associationExportJoin, association)
	return count, err
}

func (r *AssociationTransfer) Exports(ctx context.Context, association string, limit int) ([]domain.AssociationExportRow, error) {
	var rows []domain.AssociationExportRow
	err := r.db.SelectContext(ctx, &rows, `SELECT a.sport_association_documents_archive_id::text AS archive_id,
       d.document_id::text AS document_id, d.filename, d.filepath, d.file_size_bytes, a.date, d.creation_date`+
		associationExportJoin+` ORDER BY a.date DESC LIMIT $2`, association, limit)
	return rows, err
}

func (r *AssociationTransfer) ExportArchive(ctx context.Context, association, document string) (bool, error) {
	var found bool
	err := r.db.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1`+associationExportJoin+` AND a.document_id=$2::uuid)`, association, document)
	return found, err
}

func (r *AssociationTransfer) PrimaryOwner(ctx context.Context) (bool, *string, error) {
	var row struct {
		Owner *string `db:"owner"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT a.user_id::text AS owner FROM instance_instanceconfiguration c
  LEFT JOIN application_sportassociation a ON a.sport_association_id=c.primary_association_id ORDER BY c.id LIMIT 1`)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil, nil
	}
	if err != nil {
		return false, nil, err
	}
	return true, row.Owner, nil
}

func (r *AssociationTransfer) TaskResult(ctx context.Context, task string) (*domain.AssociationTaskResult, error) {
	var row domain.AssociationTaskResult
	err := r.db.GetContext(ctx, &row, `SELECT status, result, meta, content_encoding FROM django_celery_results_taskresult WHERE task_id=$1`, task)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (r *AssociationTransfer) CacheKeysExist(ctx context.Context, keys ...string) (bool, error) {
	count, err := r.cache.Exists(ctx, keys...).Result()
	return count > 0, err
}
