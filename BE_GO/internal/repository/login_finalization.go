package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type LoginFinalizations struct{ db *sqlx.DB }

func NewLoginFinalizations(db *sqlx.DB) *LoginFinalizations { return &LoginFinalizations{db: db} }
func (r *LoginFinalizations) BeginLoginFinalization(ctx context.Context) (domain.LoginFinalizationTransaction, error) {
	tx, err := r.db.BeginTxx(ctx, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &loginFinalizationTransaction{loginPreparationTransaction{tx: tx}}, nil
}

// Shared user-audit association lookup and transaction conflict classification.
type loginFinalizationTransaction struct{ loginPreparationTransaction }

func (t *loginFinalizationTransaction) FinalizationForUpdate(ctx context.Context, id string) (domain.LoginFinalizationState, error) {
	var state domain.LoginFinalizationState
	err := t.tx.GetContext(ctx, &state, `SELECT user_id,first_name,last_name,delete_on FROM bakney_user WHERE user_id=$1 FOR UPDATE`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return state, domain.ErrAccountNotFound
	}
	return state, credentialTransactionError(err)
}
func (t *loginFinalizationTransaction) SetLoginTime(ctx context.Context, id string, at time.Time, clear bool) error {
	result, err := t.tx.ExecContext(ctx, `UPDATE bakney_user SET last_login=$2,delete_on=CASE WHEN $3 THEN NULL ELSE delete_on END WHERE user_id=$1`, id, at, clear)
	if err != nil {
		return credentialTransactionError(err)
	}
	n, err := result.RowsAffected()
	if err == nil && n != 1 {
		return domain.ErrAccountNotFound
	}
	return credentialTransactionError(err)
}
func (t *loginFinalizationTransaction) AppendLoginDeletionAudit(ctx context.Context, event domain.LoginDeletionAudit) error {
	return credentialTransactionError(appendModelAudit(ctx, t.tx, modelAuditEntry{
		Model: "user", Key: event.AccountID, Representation: event.ObjectRepresentation, Action: 1,
		Changes: map[string][2]string{"delete_on": {event.Before, "None"}}, Context: event.Context,
		AssociationID: event.AssociationID, ResolutionPath: event.ResolutionPath, At: event.At,
	}))
}
