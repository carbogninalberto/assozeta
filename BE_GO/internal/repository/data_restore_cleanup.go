package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// DataRestoreCleanups backs the cleanup_data_restores job
// (BE/instance/tasks.py, BE/instance/restore/execution.py).
type DataRestoreCleanups struct{ db *sqlx.DB }

func NewDataRestoreCleanups(db *sqlx.DB) *DataRestoreCleanups { return &DataRestoreCleanups{db: db} }

func (r *DataRestoreCleanups) BeginDataRestoreCleanup(ctx context.Context, job domain.Job) (domain.DataRestoreCleanupTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	if err = fenceJobEffects(ctx, tx.tx, job.ID, job.LeaseToken); err != nil {
		_ = tx.Rollback()
		return nil, err
	}
	return &dataRestoreCleanupTransaction{ownedTransaction: tx}, nil
}

type dataRestoreCleanupTransaction struct{ *ownedTransaction }

// The SQL of DataRestore.objects.exclude(state__in=DataRestore.ACTIVE): no
// ORDER BY, so PostgreSQL decides the order, as for the Django iterator.
const dataRestoreTerminal = ` FROM instance_datarestore WHERE NOT (state IN ('queued', 'running'))`

func (t *dataRestoreCleanupTransaction) Terminal(ctx context.Context, backups bool) ([]domain.DataRestoreReceipt, error) {
	query := `SELECT ` + dataRestoreColumns + dataRestoreTerminal
	if backups {
		query += ` AND NOT (backup_path = '') ORDER BY created_at DESC, id DESC`
	}
	var rows []dataRestoreRow
	err := t.tx.SelectContext(ctx, &rows, query)
	return dataRestoreReceipts(rows), err
}

func (t *dataRestoreCleanupTransaction) CompletedWithMedia(ctx context.Context) ([]domain.DataRestoreReceipt, error) {
	var rows []dataRestoreRow
	err := t.tx.SelectContext(ctx, &rows, `SELECT `+dataRestoreColumns+dataRestoreTerminal+
		` AND state = 'completed' AND NOT (media = '{}'::jsonb)`)
	return dataRestoreReceipts(rows), err
}

// ReferencedKeys: Document._base_manager filepaths under restores/, and
// Subscription._base_manager signature keys under restores/ or URLs
// containing /restores/ (the URL contributes restores/ plus its remainder).
func (t *dataRestoreCleanupTransaction) ReferencedKeys(ctx context.Context) (map[string]bool, error) {
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

func (t *dataRestoreCleanupTransaction) Save(ctx context.Context, id string, changes []domain.DataRestoreColumn, at time.Time) error {
	columns := make(DataRestoreChanges, len(changes))
	for i, change := range changes {
		columns[i] = DataRestoreChange{Column: change.Column, Value: change.Value}
	}
	return dataRestoreSave(ctx, t.tx, id, columns, at)
}

func (t *dataRestoreCleanupTransaction) CompleteJob(ctx context.Context, id, token uuid.UUID, result json.RawMessage) error {
	return completeJob(ctx, t.tx, id, token, result)
}
func (t *dataRestoreCleanupTransaction) Commit() error   { return t.ownedTransaction.Commit() }
func (t *dataRestoreCleanupTransaction) Rollback() error { return t.ownedTransaction.Rollback() }
