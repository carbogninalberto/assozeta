package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// AccountReader is deliberately global only for credential/identity lookup.
// Business repositories must add association/group predicates of their own.
type AccountReader interface {
	ByID(context.Context, string) (domain.Account, error)
	VisitLoginCandidates(context.Context, bool, string, func(domain.Account) error) error
}

type Accounts struct{ db *sqlx.DB }

func NewAccounts(db *sqlx.DB) *Accounts { return &Accounts{db: db} }

var _ AccountReader = (*Accounts)(nil)

const accountColumns = `user_id, username, email, password, role, is_active, deleted,
 is_superuser, two_fa AS account_two_fa, connected_user_id, collaborator_role,
 COALESCE(collaborator_permissions, 'null'::jsonb) AS collaborator_permissions`

func (r *Accounts) ByID(ctx context.Context, id string) (domain.Account, error) {
	if err := ctx.Err(); err != nil {
		return domain.Account{}, err
	}
	var result domain.Account
	err := r.db.GetContext(ctx, &result, `SELECT `+accountColumns+` FROM bakney_user WHERE user_id = $1 AND NOT deleted`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.Account{}, domain.ErrAccountNotFound
	}
	return result, err
}

// VisitLoginCandidates follows Django's case-insensitive email/username lookup,
// preserving duplicate legacy emails and ID order. Inactive users remain in the
// candidate set; the service checks credentials/ambiguity before account status.
// Streaming bounds repository memory, but holds one connection while visiting.
// Login admission must bound concurrent KDF work. Do not perform writes on this
// same connection from a visitor; the login transaction design remains separate.
func (r *Accounts) VisitLoginCandidates(ctx context.Context, byEmail bool, identifier string, visit func(domain.Account) error) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	column := "username"
	if byEmail {
		column = "email"
	}
	// Only the fixed column above is interpolated. The entire identifier stays a
	// bound value: '%' and '_' are literals, not ILIKE wildcard operators.
	rows, err := r.db.QueryxContext(ctx, `SELECT `+accountColumns+` FROM bakney_user WHERE NOT deleted AND UPPER(`+column+`::text) = UPPER($1) ORDER BY user_id`, identifier)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		if err := ctx.Err(); err != nil {
			return err
		}
		var account domain.Account
		if err := rows.StructScan(&account); err != nil {
			return err
		}
		if err := visit(account); err != nil {
			return err
		}
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	return rows.Err()
}
