package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type ProfileTables struct{ db *sqlx.DB }

func NewProfileTables(db *sqlx.DB) *ProfileTables { return &ProfileTables{db} }
func (r *ProfileTables) BeginProfileTables(ctx context.Context) (domain.ProfileTablesTransaction, error) {
	tx, err := r.db.BeginTxx(ctx, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &profileTablesTransaction{profileUpdateTransaction{loginPreparationTransaction{tx: tx}}}, nil
}

type profileTablesTransaction struct{ profileUpdateTransaction }

func (t *profileTablesTransaction) TablePreferencesForUpdate(ctx context.Context, id string) (domain.ProfileTablePreferences, error) {
	var row domain.ProfileTablePreferences
	err := t.tx.GetContext(ctx, &row, `SELECT user_id,first_name,last_name,tables_settings FROM bakney_user WHERE user_id=$1 AND NOT deleted FOR UPDATE`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return row, domain.ErrAccountNotFound
	}
	return row, credentialTransactionError(err)
}
func (t *profileTablesTransaction) SetTablePreferences(ctx context.Context, id string, value json.RawMessage) error {
	var stored any
	if value != nil {
		if !json.Valid(value) {
			return domain.ErrInvalidScopedRequest
		}
		stored = string(value)
	}
	result, err := t.tx.ExecContext(ctx, `UPDATE bakney_user SET tables_settings=$2::jsonb WHERE user_id=$1 AND NOT deleted`, id, stored)
	if err != nil {
		return credentialTransactionError(err)
	}
	count, err := result.RowsAffected()
	if err == nil && count != 1 {
		return domain.ErrAccountNotFound
	}
	return credentialTransactionError(err)
}
