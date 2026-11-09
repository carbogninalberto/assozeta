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
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// ConsumerRestoreInstance backs instance.restore.execute on the Celery
// consumer (BE/instance/restore/execution.py): the receipt, the instance
// configuration, the Go transaction barrier and save_operation.
type ConsumerRestoreInstance struct{ db *sqlx.DB }

func NewConsumerRestoreInstance(db *sqlx.DB) *ConsumerRestoreInstance {
	return &ConsumerRestoreInstance{db: db}
}

// Barrier is server_barrier.drain_go_transactions on PostgreSQL: a short
// transaction that sets a 5 s statement timeout and takes the exclusive
// advisory transaction lock the Go transactions share. Any database error
// is the Busy it raises.
func (r *ConsumerRestoreInstance) Barrier(ctx context.Context) error {
	tx, err := r.db.BeginTxx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(ctx, `SELECT set_config('statement_timeout', $1, true)`, "5000ms"); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `SELECT pg_advisory_xact_lock($1, $2)`, 1095981639, 1381192786); err != nil {
		return err
	}
	return tx.Commit()
}

// Receipt is DataRestore.objects.get(pk=id) (nil when missing).
func (r *ConsumerRestoreInstance) Receipt(ctx context.Context, q dataRestoreQuerier, id string) (*domain.DataRestoreReceipt, error) {
	if q == nil {
		q = r.db
	}
	return dataRestoreOne(ctx, q, `SELECT `+dataRestoreColumns+` FROM instance_datarestore WHERE id = $1::uuid`, id)
}

// RestoreConfiguration is InstanceConfiguration.objects
// .select_related('primary_association__user') with the owner flags
// is_instance_administrator reads.
type RestoreConfiguration struct {
	SelfHosted         bool           `db:"self_hosted"`
	Multiple           bool           `db:"support_multiple_associations"`
	Primary            sql.NullString `db:"primary_association_id"`
	PrimaryOwner       sql.NullString `db:"primary_owner_id"`
	PrimaryDeleted     sql.NullBool   `db:"primary_deleted"`
	PrimaryOwnerExists bool           `db:"primary_owner_exists"`
}

// Configurations lists every configuration row (get() needs exactly one).
func (r *ConsumerRestoreInstance) Configurations(ctx context.Context) ([]RestoreConfiguration, error) {
	var rows []RestoreConfiguration
	err := r.db.SelectContext(ctx, &rows, `SELECT c.self_hosted, c.support_multiple_associations,
 c.primary_association_id::text AS primary_association_id, a.user_id::text AS primary_owner_id, a.deleted AS primary_deleted,
 (u.user_id IS NOT NULL) AS primary_owner_exists
 FROM instance_instanceconfiguration c
 LEFT JOIN application_sportassociation a ON a.sport_association_id = c.primary_association_id
 LEFT JOIN bakney_user u ON u.user_id = a.user_id ORDER BY c.id`)
	return rows, err
}

// OtherAssociations is SportAssociation.original_objects.exclude(pk=primary).exists().
func (r *ConsumerRestoreInstance) OtherAssociations(ctx context.Context, primary string) (bool, error) {
	return dataRestoreOtherAssociations(ctx, r.db, primary)
}

// Account is the receipt owner's is_active, deleted and is_superuser.
func (r *ConsumerRestoreInstance) Account(ctx context.Context, id string) (*domain.Account, error) {
	return NewDataRestores(r.db).Account(ctx, id)
}

// RestoreChange is one keyword of a save_operation call.
type RestoreChange struct {
	Column string
	Value  any
}

var consumerRestoreColumns = map[string]string{"state": "", "stage": "", "upload_path": "", "backup_path": "", "backup_sha256": "",
	"error": "", "attempts": "::integer", "completed_at": "::timestamptz", "media": "::jsonb"}

// Save is save_operation(op, **changes): DataRestore.objects.filter(pk=id)
// .update(**changes, updated_at=now) on q (the database or a transaction).
func (r *ConsumerRestoreInstance) Save(ctx context.Context, q sqlx.ExecerContext, id string, changes []RestoreChange, now time.Time) error {
	if q == nil {
		q = r.db
	}
	assignments := make([]string, 0, len(changes)+1)
	args := []any{id}
	for _, change := range changes {
		cast, ok := consumerRestoreColumns[change.Column]
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

// DetachedUsers is User.original_objects.filter(pk__in=keys,
// connected_user=owner).exclude(pk=owner).values_list('pk').
func (r *ConsumerRestoreInstance) DetachedUsers(ctx context.Context, tx *sqlx.Tx, keys []string, owner string) ([]string, error) {
	if len(keys) == 0 {
		return nil, nil
	}
	placeholders := make([]string, len(keys))
	args := []any{owner}
	for i, key := range keys {
		args = append(args, key)
		placeholders[i] = "$" + strconv.Itoa(len(args)) + "::uuid"
	}
	var ids []string
	err := tx.SelectContext(ctx, &ids, `SELECT "bakney_user"."user_id"::text FROM "bakney_user" WHERE ("bakney_user"."connected_user_id" = $1::uuid
 AND "bakney_user"."user_id" IN (`+strings.Join(placeholders, ", ")+`) AND NOT ("bakney_user"."user_id" = $1::uuid))`, args...)
	return ids, err
}

// Detach is DataRestoreUserDetachment.objects.update_or_create(user_id=id,
// defaults={'owner': owner, 'association_id': association}).
func (r *ConsumerRestoreInstance) Detach(ctx context.Context, tx *sqlx.Tx, id, owner, association string) error {
	var existing []int64
	if err := tx.SelectContext(ctx, &existing, `SELECT id FROM instance_datarestoreuserdetachment WHERE user_id = $1::uuid FOR UPDATE`, id); err != nil {
		return err
	}
	if len(existing) == 0 {
		_, err := tx.ExecContext(ctx, `INSERT INTO instance_datarestoreuserdetachment (user_id, owner_id, association_id)
 VALUES ($1::uuid, $2::uuid, $3::uuid)`, id, owner, association)
		return err
	}
	_, err := tx.ExecContext(ctx, `UPDATE instance_datarestoreuserdetachment SET owner_id = $2::uuid, association_id = $3::uuid
 WHERE id = $1`, existing[0], owner, association)
	return err
}

// DeactivateUsers is removed.update(connected_user=None, is_active=False).
func (r *ConsumerRestoreInstance) DeactivateUsers(ctx context.Context, tx *sqlx.Tx, ids []string) error {
	if len(ids) == 0 {
		return nil
	}
	_, err := tx.ExecContext(ctx, `UPDATE bakney_user SET connected_user_id = NULL, is_active = false WHERE user_id = ANY($1::text[]::uuid[])`, ids)
	return err
}

// Detachment reports DataRestoreUserDetachment.objects.filter(user=id,
// owner=owner, association_id=association).exists().
func (r *ConsumerRestoreInstance) Detachment(ctx context.Context, tx *sqlx.Tx, id, owner, association string) (bool, error) {
	var exists bool
	err := tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM instance_datarestoreuserdetachment WHERE user_id = $1::uuid
 AND owner_id = $2::uuid AND association_id = $3::uuid)`, id, owner, association)
	return exists, err
}

// DeleteDetachment is that queryset's delete() (no signals, no audit).
func (r *ConsumerRestoreInstance) DeleteDetachment(ctx context.Context, tx *sqlx.Tx, id, owner, association string) error {
	_, err := tx.ExecContext(ctx, `DELETE FROM instance_datarestoreuserdetachment WHERE user_id = $1::uuid AND owner_id = $2::uuid
 AND association_id = $3::uuid`, id, owner, association)
	return err
}

// UserState is what restore_user_links reads of an imported user.
type UserState struct {
	ConnectedUser sql.NullString `db:"connected_user_id"`
	IsActive      bool           `db:"is_active"`
}

func (r *ConsumerRestoreInstance) UserState(ctx context.Context, tx *sqlx.Tx, id string) (UserState, error) {
	var state UserState
	err := tx.GetContext(ctx, &state, `SELECT connected_user_id::text AS connected_user_id, is_active FROM bakney_user WHERE user_id = $1::uuid`, id)
	return state, err
}

// UpdateUser is User.original_objects.filter(pk=id).update(**fields): no
// save(), no signal, no audit. Values are text (or nil) cast to the column.
func (r *ConsumerRestoreInstance) UpdateUser(ctx context.Context, tx *sqlx.Tx, id string, columns []string, casts []string, values []any) error {
	if len(columns) == 0 {
		return nil
	}
	sets := make([]string, len(columns))
	args := []any{id}
	for i, column := range columns {
		args = append(args, values[i])
		sets[i] = `"` + column + `" = $` + strconv.Itoa(len(args)) + casts[i]
	}
	_, err := tx.ExecContext(ctx, `UPDATE bakney_user SET `+strings.Join(sets, ", ")+` WHERE user_id = $1::uuid`, args...)
	return err
}

// ConsumerRestoreCleanups is the cleanup and pruning store of
// instance.restore.execute: plain READ COMMITTED transactions (the task
// holds the exclusive restore lock; there is no worker job to fence).
type ConsumerRestoreCleanups struct{ db *sqlx.DB }

func NewConsumerRestoreCleanups(db *sqlx.DB) *ConsumerRestoreCleanups {
	return &ConsumerRestoreCleanups{db: db}
}

func (r *ConsumerRestoreCleanups) BeginDataRestoreCleanup(ctx context.Context, _ domain.Job) (domain.DataRestoreCleanupTransaction, error) {
	tx, err := r.db.BeginTxx(ctx, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &consumerRestoreCleanupTx{tx: tx}, nil
}

type consumerRestoreCleanupTx struct{ tx *sqlx.Tx }

func (t *consumerRestoreCleanupTx) Terminal(ctx context.Context, backups bool) ([]domain.DataRestoreReceipt, error) {
	query := `SELECT ` + dataRestoreColumns + dataRestoreTerminal
	if backups {
		query += ` AND NOT (backup_path = '') ORDER BY created_at DESC, id DESC`
	}
	var rows []dataRestoreRow
	err := t.tx.SelectContext(ctx, &rows, query)
	return dataRestoreReceipts(rows), err
}

func (t *consumerRestoreCleanupTx) CompletedWithMedia(ctx context.Context) ([]domain.DataRestoreReceipt, error) {
	var rows []dataRestoreRow
	err := t.tx.SelectContext(ctx, &rows, `SELECT `+dataRestoreColumns+dataRestoreTerminal+
		` AND state = 'completed' AND NOT (media = '{}'::jsonb)`)
	return dataRestoreReceipts(rows), err
}

func (t *consumerRestoreCleanupTx) ReferencedKeys(ctx context.Context) (map[string]bool, error) {
	keys := map[string]bool{}
	var documents []sql.NullString
	if err := t.tx.SelectContext(ctx, &documents, `SELECT filepath FROM docmanager_document WHERE filepath::text LIKE 'restores/%'`); err != nil {
		return nil, err
	}
	for _, key := range documents {
		if key.Valid {
			keys[key.String] = true
		}
	}
	var signatures []struct {
		Key sql.NullString `db:"signature_storage_key"`
		URL sql.NullString `db:"signature_url"`
	}
	if err := t.tx.SelectContext(ctx, &signatures, `SELECT signature_storage_key, signature_url FROM application_subscription
 WHERE (signature_storage_key::text LIKE 'restores/%' OR signature_url::text LIKE '%/restores/%')`); err != nil {
		return nil, err
	}
	for _, row := range signatures {
		if row.Key.Valid {
			keys[row.Key.String] = true
		}
		if row.URL.Valid && row.URL.String != "" {
			if _, rest, found := strings.Cut(row.URL.String, "/restores/"); found {
				keys["restores/"+rest] = true
			}
		}
	}
	return keys, nil
}

func (t *consumerRestoreCleanupTx) Save(ctx context.Context, id string, changes []domain.DataRestoreColumn, at time.Time) error {
	columns := make(DataRestoreChanges, len(changes))
	for i, change := range changes {
		columns[i] = DataRestoreChange{Column: change.Column, Value: change.Value}
	}
	return dataRestoreSave(ctx, t.tx, id, columns, at)
}

func (t *consumerRestoreCleanupTx) CompleteJob(context.Context, uuid.UUID, uuid.UUID, json.RawMessage) error {
	return nil
}
func (t *consumerRestoreCleanupTx) Commit() error   { return t.tx.Commit() }
func (t *consumerRestoreCleanupTx) Rollback() error { return t.tx.Rollback() }
