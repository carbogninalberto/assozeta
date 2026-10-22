package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type TwoFactorUpdates struct{ db *sqlx.DB }

func NewTwoFactorUpdates(db *sqlx.DB) *TwoFactorUpdates { return &TwoFactorUpdates{db} }
func (r *TwoFactorUpdates) BeginTwoFactorUpdate(ctx context.Context) (domain.TwoFactorUpdateTransaction, error) {
	tx, err := r.db.BeginTxx(ctx, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &twoFactorUpdateTransaction{loginPreparationTransaction{tx: tx}}, nil
}

type twoFactorUpdateTransaction struct{ loginPreparationTransaction }

func (t *twoFactorUpdateTransaction) TwoFactorForUpdate(ctx context.Context, id string) (domain.TwoFactorState, error) {
	var state domain.TwoFactorState
	err := t.tx.GetContext(ctx, &state, `SELECT user_id,first_name,last_name,two_fa,two_fa_secret FROM bakney_user WHERE user_id=$1 FOR UPDATE`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return state, domain.ErrAccountNotFound
	}
	return state, credentialTransactionError(err)
}
func (t *twoFactorUpdateTransaction) SetTwoFactor(ctx context.Context, id string, enabled bool, secret *string) error {
	result, err := t.tx.ExecContext(ctx, `UPDATE bakney_user SET two_fa=$2,two_fa_secret=CASE WHEN $2 THEN $3 ELSE two_fa_secret END WHERE user_id=$1`, id, enabled, secret)
	if err != nil {
		return credentialTransactionError(err)
	}
	count, err := result.RowsAffected()
	if err == nil && count != 1 {
		return domain.ErrAccountNotFound
	}
	return credentialTransactionError(err)
}
func (t *twoFactorUpdateTransaction) AppendTwoFactorAudit(ctx context.Context, event domain.TwoFactorAudit) error {
	return credentialTransactionError(appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "user", Key: event.AccountID, Representation: event.ObjectRepresentation, Action: 1, Changes: event.Changes, Context: event.Context, AssociationID: event.AssociationID, ResolutionPath: event.ResolutionPath, At: event.At}))
}
