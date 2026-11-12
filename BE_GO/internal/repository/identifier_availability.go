package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type IdentifierAccounts struct{ db *sqlx.DB }

func NewIdentifierAccounts(db *sqlx.DB) *IdentifierAccounts { return &IdentifierAccounts{db: db} }

// These two legacy public availability endpoints intentionally look across
// accounts. Do not reuse these queries for tenant-bound business resources.
// Django User.objects excludes deleted rows but retains inactive accounts.
func (r *IdentifierAccounts) FirstMatchingIdentifier(ctx context.Context, byEmail bool, value string) (*domain.IdentifierAccount, error) {
	column := "username"
	if byEmail {
		column = "email"
	}
	var result domain.IdentifierAccount
	err := r.db.GetContext(ctx, &result, `SELECT user_id,first_name,last_name,email FROM bakney_user WHERE NOT deleted AND UPPER(`+column+`::text)=UPPER($1) ORDER BY user_id LIMIT 1`, value)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &result, nil
}
func (r *IdentifierAccounts) UsernameExists(ctx context.Context, value string) (bool, error) {
	var exists bool
	err := r.db.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM bakney_user WHERE NOT deleted AND UPPER(username::text)=UPPER($1))`, value)
	return exists, err
}
