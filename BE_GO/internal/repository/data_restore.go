package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// DataRestores reads and writes the instance_datarestore receipts of the
// data-restore administration views (BE/instance/restore/views.py).
type DataRestores struct{ db *sqlx.DB }

func NewDataRestores(db *sqlx.DB) *DataRestores { return &DataRestores{db: db} }

const dataRestoreColumns = `id::text AS id, owner_id::text AS owner_id, association_id::text AS association_id, state, stage,
 completed_at, created_at, updated_at, upload_path, sha256, version, preview::text AS preview, backup_path, backup_sha256,
 media::text AS media, error, allow_missing_media, attempts`

type dataRestoreQuerier interface {
	GetContext(ctx context.Context, dest any, query string, args ...any) error
	SelectContext(ctx context.Context, dest any, query string, args ...any) error
	ExecContext(ctx context.Context, query string, args ...any) (sql.Result, error)
}

type dataRestoreRow struct {
	domain.DataRestoreReceipt
	PreviewText string `db:"preview"`
	MediaText   string `db:"media"`
}

func dataRestoreReceipts(rows []dataRestoreRow) []domain.DataRestoreReceipt {
	out := make([]domain.DataRestoreReceipt, 0, len(rows))
	for _, row := range rows {
		receipt := row.DataRestoreReceipt
		receipt.Preview, receipt.Media = json.RawMessage(row.PreviewText), json.RawMessage(row.MediaText)
		out = append(out, receipt)
	}
	return out
}

func dataRestoreOne(ctx context.Context, q dataRestoreQuerier, query string, args ...any) (*domain.DataRestoreReceipt, error) {
	var rows []dataRestoreRow
	if err := q.SelectContext(ctx, &rows, query, args...); err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return nil, nil
	}
	receipt := dataRestoreReceipts(rows)[0]
	return &receipt, nil
}

// Instance is InstanceConfiguration.get_config() (objects.first(): the lowest
// pk) with the primary association's owner; nil without a configuration.
func (r *DataRestores) Instance(ctx context.Context) (*domain.DataRestoreInstance, error) {
	return dataRestoreInstance(ctx, r.db)
}

func dataRestoreInstance(ctx context.Context, q dataRestoreQuerier) (*domain.DataRestoreInstance, error) {
	var rows []domain.DataRestoreInstance
	err := q.SelectContext(ctx, &rows, `SELECT c.self_hosted, c.support_multiple_associations,
 c.primary_association_id::text AS primary_association_id, a.user_id::text AS primary_owner_id
 FROM instance_instanceconfiguration c LEFT JOIN application_sportassociation a ON a.sport_association_id = c.primary_association_id
 ORDER BY c.id ASC LIMIT 1`)
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	return &rows[0], nil
}

// OtherAssociations is SportAssociation.original_objects.exclude(pk=primary).exists().
func (r *DataRestores) OtherAssociations(ctx context.Context, primary string) (bool, error) {
	return dataRestoreOtherAssociations(ctx, r.db, primary)
}

func dataRestoreOtherAssociations(ctx context.Context, q dataRestoreQuerier, primary string) (bool, error) {
	var exists bool
	err := q.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_sportassociation WHERE NOT (sport_association_id = $1::uuid))`, primary)
	return exists, err
}

// Pending is restore_pending(): any receipt in DataRestore.ACTIVE.
func (r *DataRestores) Pending(ctx context.Context) (bool, error) {
	return dataRestorePending(ctx, r.db)
}

func dataRestorePending(ctx context.Context, q dataRestoreQuerier) (bool, error) {
	var pending bool
	err := q.GetContext(ctx, &pending, `SELECT EXISTS(SELECT 1 FROM instance_datarestore WHERE state IN ('queued', 'running'))`)
	return pending, err
}

// History is filter(owner=user).order_by('-created_at')[:10].
func (r *DataRestores) History(ctx context.Context, owner string) ([]domain.DataRestoreReceipt, error) {
	var rows []dataRestoreRow
	err := r.db.SelectContext(ctx, &rows, `SELECT `+dataRestoreColumns+` FROM instance_datarestore WHERE owner_id = $1::uuid
 ORDER BY created_at DESC LIMIT 10`, owner)
	return dataRestoreReceipts(rows), err
}

// Active is filter(state__in=ACTIVE, owner=user).first() (ordered by pk).
func (r *DataRestores) Active(ctx context.Context, owner string) (*domain.DataRestoreReceipt, error) {
	return dataRestoreOne(ctx, r.db, `SELECT `+dataRestoreColumns+` FROM instance_datarestore
 WHERE state IN ('queued', 'running') AND owner_id = $1::uuid ORDER BY id ASC LIMIT 1`, owner)
}

// Owned is DataRestore.objects.filter(owner=user, pk=id) (get_object_or_404).
func (r *DataRestores) Owned(ctx context.Context, id, owner string) (*domain.DataRestoreReceipt, error) {
	return dataRestoreOne(ctx, r.db, `SELECT `+dataRestoreColumns+` FROM instance_datarestore WHERE id = $1::uuid AND owner_id = $2::uuid`, id, owner)
}

// Get is DataRestore.objects.select_related('owner') by pk.
func (r *DataRestores) Get(ctx context.Context, id string) (*domain.DataRestoreReceipt, error) {
	return dataRestoreOne(ctx, r.db, `SELECT `+dataRestoreColumns+` FROM instance_datarestore d
 WHERE id = $1::uuid AND EXISTS(SELECT 1 FROM bakney_user u WHERE u.user_id = d.owner_id)`, id)
}

// Account is the receipt owner's flags is_instance_administrator reads.
func (r *DataRestores) Account(ctx context.Context, id string) (*domain.Account, error) {
	var rows []domain.Account
	err := r.db.SelectContext(ctx, &rows, `SELECT user_id::text AS user_id, is_active, deleted, is_superuser FROM bakney_user WHERE user_id = $1::uuid`, id)
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	return &rows[0], nil
}

// RecoveryPage is recovery_page's queryset: the owner's receipts with a
// backup, before the cursor (created_at, id), newest first, eleven rows.
func (r *DataRestores) RecoveryPage(ctx context.Context, owner string, cursor *domain.DataRestoreReceipt) ([]domain.DataRestoreReceipt, error) {
	query := `SELECT ` + dataRestoreColumns + ` FROM instance_datarestore WHERE owner_id = $1::uuid AND NOT (backup_path = '')`
	args := []any{owner}
	if cursor != nil {
		query += ` AND (created_at < $2 OR (created_at = $2 AND id < $3::uuid))`
		args = append(args, cursor.CreatedAt, cursor.ID)
	}
	var rows []dataRestoreRow
	err := r.db.SelectContext(ctx, &rows, query+` ORDER BY created_at DESC, id DESC LIMIT 11`, args...)
	return dataRestoreReceipts(rows), err
}

// Save is save_operation(op, **changes) outside a transaction: one UPDATE
// with updated_at.
func (r *DataRestores) Save(ctx context.Context, id string, changes DataRestoreChanges, now time.Time) error {
	return dataRestoreSave(ctx, r.db, id, changes, now)
}

// DataRestoreChanges are the columns one save_operation call writes, in order.
type DataRestoreChanges []DataRestoreChange

type DataRestoreChange struct {
	Column string
	Value  any
}

var dataRestoreWritable = map[string]string{"state": "", "stage": "", "upload_path": "", "backup_path": "", "backup_sha256": "",
	"allow_missing_media": "", "preview": "::jsonb", "media": "::jsonb", "error": ""}

func dataRestoreSave(ctx context.Context, q dataRestoreQuerier, id string, changes DataRestoreChanges, now time.Time) error {
	assignments := make([]string, 0, len(changes)+1)
	args := []any{id}
	for _, change := range changes {
		cast, ok := dataRestoreWritable[change.Column]
		if !ok {
			return errors.New("data restore column not writable: " + change.Column)
		}
		args = append(args, change.Value)
		assignments = append(assignments, change.Column+" = $"+strconv.Itoa(len(args))+cast)
	}
	args = append(args, now)
	assignments = append(assignments, "updated_at = $"+strconv.Itoa(len(args)))
	_, err := q.ExecContext(ctx, `UPDATE instance_datarestore SET `+strings.Join(assignments, ", ")+` WHERE id = $1::uuid`, args...)
	return err
}

// DataRestoreTx is the transaction.atomic() block of DataRestoreActionView.post.
type DataRestoreTx struct{ owned *ownedTransaction }

// Begin starts the owned transaction (the Go restore barrier's shared lock).
func (r *DataRestores) Begin(ctx context.Context) (*DataRestoreTx, error) {
	owned, err := beginOwnedTransaction(ctx, r.db, nil)
	if err != nil {
		return nil, err
	}
	return &DataRestoreTx{owned: owned}, nil
}

// Lock is select_for_update().get(pk=id, owner=user).
func (t *DataRestoreTx) Lock(ctx context.Context, id, owner string) (*domain.DataRestoreReceipt, error) {
	return dataRestoreOne(ctx, t.owned.tx, `SELECT `+dataRestoreColumns+` FROM instance_datarestore
 WHERE id = $1::uuid AND owner_id = $2::uuid FOR UPDATE`, id, owner)
}

func (t *DataRestoreTx) Instance(ctx context.Context) (*domain.DataRestoreInstance, error) {
	return dataRestoreInstance(ctx, t.owned.tx)
}

func (t *DataRestoreTx) OtherAssociations(ctx context.Context, primary string) (bool, error) {
	return dataRestoreOtherAssociations(ctx, t.owned.tx, primary)
}

func (t *DataRestoreTx) Pending(ctx context.Context) (bool, error) {
	return dataRestorePending(ctx, t.owned.tx)
}

func (t *DataRestoreTx) Save(ctx context.Context, id string, changes DataRestoreChanges, now time.Time) error {
	return dataRestoreSave(ctx, t.owned.tx, id, changes, now)
}

func (t *DataRestoreTx) Commit() error   { return t.owned.Commit() }
func (t *DataRestoreTx) Rollback() error { return t.owned.Rollback() }

// Reviews is DataRestore.objects.filter(owner=user, state='review') (unordered; by pk here).
func (r *DataRestores) Reviews(ctx context.Context, owner string) ([]domain.DataRestoreReceipt, error) {
	var rows []dataRestoreRow
	err := r.db.SelectContext(ctx, &rows, `SELECT `+dataRestoreColumns+` FROM instance_datarestore
 WHERE owner_id = $1::uuid AND state = 'review' ORDER BY id`, owner)
	return dataRestoreReceipts(rows), err
}

// Create is DataRestore.objects.create(owner, association_id, sha256,
// version, preview) with the model defaults; created_at and updated_at are now.
func (r *DataRestores) Create(ctx context.Context, id, owner, association, sha256, version, preview string, now time.Time) (*domain.DataRestoreReceipt, error) {
	if _, err := r.db.ExecContext(ctx, `INSERT INTO instance_datarestore
 (id, owner_id, association_id, state, stage, completed_at, created_at, updated_at, upload_path, sha256, version, preview,
  backup_path, backup_sha256, media, error, allow_missing_media, attempts)
 VALUES ($1::uuid, $2::uuid, $3::uuid, 'review', 'validated', NULL, $4, $4, '', $5, $6, $7::jsonb, '', '', '{}'::jsonb, '', FALSE, 0)`,
		id, owner, association, now, sha256, version, preview); err != nil {
		return nil, err
	}
	return dataRestoreOne(ctx, r.db, `SELECT `+dataRestoreColumns+` FROM instance_datarestore WHERE id = $1::uuid`, id)
}
