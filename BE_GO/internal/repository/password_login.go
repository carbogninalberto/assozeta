package repository

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jmoiron/sqlx"
)

// PasswordLogins owns only credential-stage password writes. The same table and
// Django encodings remain authoritative; password is excluded by Django auditlog.
type PasswordLogins struct{ db *sqlx.DB }

func NewPasswordLogins(db *sqlx.DB) *PasswordLogins { return &PasswordLogins{db: db} }
func (s *PasswordLogins) BeginPasswordLogin(ctx context.Context) (domain.PasswordLoginTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, s.db, &sql.TxOptions{Isolation: sql.LevelRepeatableRead})
	if err != nil {
		return nil, err
	}
	return &passwordLoginTransaction{ownedTransaction: tx}, nil
}

type passwordLoginTransaction struct{ *ownedTransaction }

func (t *passwordLoginTransaction) Candidates(ctx context.Context, byEmail bool, identifier, after string) ([]domain.Account, error) {
	column := "username"
	if byEmail {
		column = "email"
	}
	query := `SELECT ` + accountColumns + ` FROM bakney_user WHERE NOT deleted AND UPPER(` + column + `::text)=UPPER($1)`
	args := []any{identifier}
	if after != "" {
		query += ` AND user_id > $2::uuid`
		args = append(args, after)
	}
	// Keep one repeatable-read candidate snapshot across pages without locking
	// read-only credentials. FOR UPDATE makes an unrelated concurrent last_login
	// write invalidate that snapshot and can exhaust the service's retry budget.
	// Actual password UPDATEs still acquire row locks and PostgreSQL rejects any
	// change since this snapshot, so a stale rehash cannot overwrite a reset.
	query += ` ORDER BY user_id LIMIT 128`
	rows := []domain.Account{}
	err := t.tx.SelectContext(ctx, &rows, query, args...)
	return rows, credentialTransactionError(err)
}
func (t *passwordLoginTransaction) ReplacePasswords(ctx context.Context, rows []domain.PasswordReplacement) error {
	if len(rows) == 0 {
		return nil
	}
	if len(rows) > domain.PasswordCandidatePage {
		return errors.New("password update batch exceeds page size")
	}
	values := make([]string, len(rows))
	args := make([]any, 0, len(rows)*2)
	for i, row := range rows {
		values[i] = fmt.Sprintf("($%d::uuid,$%d::text)", i*2+1, i*2+2)
		args = append(args, row.ID, row.Encoded)
	}
	result, err := t.tx.ExecContext(ctx, `UPDATE bakney_user AS u SET password = v.password FROM (VALUES `+strings.Join(values, ",")+`) AS v(user_id,password) WHERE u.user_id=v.user_id`, args...)
	if err != nil {
		return credentialTransactionError(err)
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return credentialTransactionError(err)
	}
	if affected != int64(len(rows)) {
		return errors.New("password update lost a credential candidate")
	}
	return nil
}
func (t *passwordLoginTransaction) Commit() error {
	return credentialTransactionError(t.ownedTransaction.Commit())
}
func (t *passwordLoginTransaction) Rollback() error { return t.ownedTransaction.Rollback() }

func credentialTransactionError(err error) error {
	var pg *pgconn.PgError
	if errors.As(err, &pg) && (pg.Code == "40001" || pg.Code == "40P01") {
		return errors.Join(domain.ErrCredentialConflict, err)
	}
	return err
}
