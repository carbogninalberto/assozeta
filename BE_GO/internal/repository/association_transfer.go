package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/associationexport"
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

// AssociationExportFile is the archive row delete_export resolves with its document.
type AssociationExportFile struct {
	ArchiveID  string  `db:"archive_id"`
	DocumentID string  `db:"document_id"`
	Filepath   *string `db:"filepath"`
}

// ExportFiles are the rows SportAssociationDocumentsArchive.objects.get(...)
// of delete_export matches (get() raises unless exactly one).
func (r *AssociationTransfer) ExportFiles(ctx context.Context, association, document string) ([]AssociationExportFile, error) {
	var rows []AssociationExportFile
	err := r.db.SelectContext(ctx, &rows, `SELECT a.sport_association_documents_archive_id::text AS archive_id,
       d.document_id::text AS document_id, d.filepath`+associationExportJoin+` AND a.document_id=$2::uuid LIMIT 21`, association, document)
	return rows, err
}

// DocumentReferences counts the rows other than archives that reference the
// document (Document.delete()'s collector would cascade, protect or clear them).
func (r *AssociationTransfer) DocumentReferences(ctx context.Context, id string) (int64, error) {
	return (&ConsumerRestoreExportStore{db: r.db}).DocumentReferences(ctx, id)
}

// ActorAssociation is SportAssociation.objects.filter(user=actor).first(),
// the auditlog index fallback for documents.
func (r *AssociationTransfer) ActorAssociation(ctx context.Context, actor string) (*string, error) {
	return (&DocumentRender{db: r.db}).ActorAssociation(ctx, actor)
}

// DeleteArchive is archive.delete() of delete_export with its audit entry.
func (r *AssociationTransfer) DeleteArchive(ctx context.Context, id, association string, audit domain.LoginAuditContext, at time.Time) error {
	tx, err := r.db.BeginTxx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	changes, _, err := deleteModelWithAudit(ctx, tx, "application_sportassociationdocumentsarchive", "sport_association_documents_archive_id", id)
	if err != nil {
		return err
	}
	path := "changes_json.sport_association"
	if err = appendModelAudit(ctx, tx, modelAuditEntry{Model: "sportassociationdocumentsarchive", Key: id,
		Representation: "SportAssociationDocumentsArchive object (" + id + ")", Action: 2, Changes: changes, Context: audit,
		AssociationID: &association, ResolutionPath: &path, At: at}); err != nil {
		return err
	}
	return tx.Commit()
}

// DeleteDocument is document.delete() of delete_export with its audit entry,
// indexed through the actor's association.
func (r *AssociationTransfer) DeleteDocument(ctx context.Context, id string, actorAssociation *string, audit domain.LoginAuditContext,
	at time.Time) error {
	tx, err := r.db.BeginTxx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	changes, _, err := deleteModelWithAuditReverse(ctx, tx, "docmanager_document", "document_id", id, documentReverse)
	if err != nil {
		return err
	}
	if err = appendModelAuditForApp(ctx, tx, "docmanager", documentAudit(id, 2, changes, actorAssociation, audit, at)); err != nil {
		return err
	}
	return tx.Commit()
}

// CacheValue is cache.get(key) (django-redis, pickled): present is false for
// a miss; values outside the unpickler's subset are ErrDataRestoreProgressShape.
func (r *AssociationTransfer) CacheValue(ctx context.Context, key string) (any, bool, error) {
	raw, err := r.cache.Get(ctx, ":1:"+key).Bytes()
	if errors.Is(err, redis.Nil) {
		return nil, false, nil
	}
	if err != nil {
		return nil, false, err
	}
	value, err := unpickleDataRestoreProgress(raw)
	return value, true, err
}

// CacheAdd is cache.add(key, value, timeout): SET NX with the timeout in
// milliseconds (django-redis); false when the key exists.
func (r *AssociationTransfer) CacheAdd(ctx context.Context, key string, value associationexport.Dict, timeout time.Duration) (bool, error) {
	data, err := PickleDict(value)
	if err != nil {
		return false, err
	}
	return r.cache.SetNX(ctx, ":1:"+key, data, timeout).Result()
}

// CacheSet is cache.set(key, value, timeout).
func (r *AssociationTransfer) CacheSet(ctx context.Context, key string, value associationexport.Dict, timeout time.Duration) error {
	data, err := PickleDict(value)
	if err != nil {
		return err
	}
	return r.cache.Set(ctx, ":1:"+key, data, timeout).Err()
}

// CacheDelete is cache.delete / cache.delete_many.
func (r *AssociationTransfer) CacheDelete(ctx context.Context, keys ...string) error {
	prefixed := make([]string, len(keys))
	for i, key := range keys {
		prefixed[i] = ":1:" + key
	}
	return r.cache.Del(ctx, prefixed...).Err()
}

// ImportConflicts are the rows ImportValidator.validate_uuid_conflicts reads.
type ImportConflicts struct {
	AssociationExists bool
	OwnerExists       bool
	// OtherAssociation is the owner's first other association (original_objects).
	OtherAssociation *string
	// Unlinked are archive users that exist here and are not linked to the owner.
	Unlinked []string
}

// ImportConflicts runs the original_objects lookups of validate_uuid_conflicts
// with canonical UUIDs: the association, the owner, the owner's other
// association and unlinked_existing_user_ids for the candidate users.
func (r *AssociationTransfer) ImportConflicts(ctx context.Context, association, owner string, candidates []string) (ImportConflicts, error) {
	var result ImportConflicts
	if err := r.db.GetContext(ctx, &result.AssociationExists, `SELECT EXISTS(SELECT 1 FROM application_sportassociation
 WHERE sport_association_id=$1::uuid)`, association); err != nil {
		return result, err
	}
	if err := r.db.GetContext(ctx, &result.OwnerExists, `SELECT EXISTS(SELECT 1 FROM bakney_user WHERE user_id=$1::uuid)`, owner); err != nil {
		return result, err
	}
	if result.OwnerExists {
		var other []string
		if err := r.db.SelectContext(ctx, &other, `SELECT sport_association_id::text FROM application_sportassociation
 WHERE user_id=$1::uuid AND NOT sport_association_id=$2::uuid ORDER BY sport_association_id LIMIT 1`, owner, association); err != nil {
			return result, err
		}
		if len(other) == 1 {
			result.OtherAssociation = &other[0]
		}
	}
	result.Unlinked = []string{}
	if len(candidates) > 0 {
		args := []any{owner}
		placeholders := make([]string, len(candidates))
		for i, candidate := range candidates {
			args = append(args, candidate)
			placeholders[i] = "$" + strconv.Itoa(i+2) + "::uuid"
		}
		if err := r.db.SelectContext(ctx, &result.Unlinked, `SELECT user_id::text FROM bakney_user
 WHERE user_id IN (`+strings.Join(placeholders, ", ")+`) AND NOT (connected_user_id IS NOT NULL AND connected_user_id=$1::uuid)
 ORDER BY user_id`, args...); err != nil {
			return result, err
		}
	}
	return result, nil
}

// SetExportSize is doc.save(update_fields=['file_size_bytes']) of
// list_exports with its update entry, indexed through the actor's association.
func (r *AssociationTransfer) SetExportSize(ctx context.Context, id string, size int64, actorAssociation *string,
	audit domain.LoginAuditContext, at time.Time) error {
	tx, err := r.db.BeginTxx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(ctx, `UPDATE docmanager_document SET file_size_bytes=$2 WHERE document_id=$1::uuid`, id, size); err != nil {
		return err
	}
	changes := map[string][2]string{"file_size_bytes": {"None", strconv.FormatInt(size, 10)}}
	if err = appendModelAuditForApp(ctx, tx, "docmanager", documentAudit(id, 1, changes, actorAssociation, audit, at)); err != nil {
		return err
	}
	return tx.Commit()
}
