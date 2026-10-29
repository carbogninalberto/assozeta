package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type SessionAccounts struct{ db *sqlx.DB }

func NewSessionAccounts(db *sqlx.DB) *SessionAccounts { return &SessionAccounts{db: db} }

// Django session backends use User._default_manager (original_objects), unlike
// the filtered User.objects used for Bearer authentication. This read is only
// for session/audit identity; never reuse it for API or resource authorization.
func (r *SessionAccounts) SessionUser(ctx context.Context, id string) (domain.SessionUser, error) {
	var user domain.SessionUser
	err := r.db.GetContext(ctx, &user, `SELECT user_id,email,password,is_active FROM bakney_user WHERE user_id=$1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return user, domain.ErrAccountNotFound
	}
	return user, err
}
