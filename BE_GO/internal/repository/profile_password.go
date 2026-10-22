package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type ProfilePasswords struct{ db *sqlx.DB }

func NewProfilePasswords(db *sqlx.DB) *ProfilePasswords { return &ProfilePasswords{db} }
func (r *ProfilePasswords) BeginProfilePassword(ctx context.Context) (domain.ProfilePasswordTransaction, error) {
	tx, err := r.db.BeginTxx(ctx, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &profilePasswordTransaction{passwordLoginTransaction{tx: tx}}, nil
}

type profilePasswordTransaction struct{ passwordLoginTransaction }

func (t *profilePasswordTransaction) PasswordForUpdate(ctx context.Context, id string) (string, error) {
	var encoded string
	err := t.tx.GetContext(ctx, &encoded, `SELECT password FROM bakney_user WHERE user_id=$1 AND NOT deleted FOR UPDATE`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return "", domain.ErrAccountNotFound
	}
	return encoded, credentialTransactionError(err)
}
func (t *profilePasswordTransaction) SetPassword(ctx context.Context, id, encoded string) error {
	result, err := t.tx.ExecContext(ctx, `UPDATE bakney_user SET password=$2 WHERE user_id=$1 AND NOT deleted`, id, encoded)
	if err != nil {
		return credentialTransactionError(err)
	}
	count, err := result.RowsAffected()
	if err == nil && count != 1 {
		return domain.ErrAccountNotFound
	}
	return credentialTransactionError(err)
}
