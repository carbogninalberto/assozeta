package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type LoginPreparations struct{ db *sqlx.DB }

func NewLoginPreparations(db *sqlx.DB) *LoginPreparations { return &LoginPreparations{db: db} }
func (r *LoginPreparations) BeginLoginPreparation(ctx context.Context) (domain.LoginPreparationTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &loginPreparationTransaction{ownedTransaction: tx}, nil
}

type loginPreparationTransaction struct{ *ownedTransaction }

func (t *loginPreparationTransaction) AccountForUpdate(ctx context.Context, id string) (domain.LoginChallenge, error) {
	var account domain.LoginChallenge
	err := t.tx.GetContext(ctx, &account, `SELECT `+accountColumns+`,first_name,last_name,two_fa,two_fa_secret FROM bakney_user WHERE user_id=$1 AND NOT deleted FOR UPDATE`, id)
	account.Password = ""
	if errors.Is(err, sql.ErrNoRows) {
		return account, domain.ErrAccountNotFound
	}
	return account, credentialTransactionError(err)
}
func (t *loginPreparationTransaction) SetUsername(ctx context.Context, id, value string) error {
	result, err := t.tx.ExecContext(ctx, `UPDATE bakney_user SET username=$1 WHERE user_id=$2`, value, id)
	if err != nil {
		return credentialTransactionError(err)
	}
	n, err := result.RowsAffected()
	if err == nil && n != 1 {
		return domain.ErrAccountNotFound
	}
	return credentialTransactionError(err)
}
func (t *loginPreparationTransaction) AuditAssociations(ctx context.Context, target string, actor *string) (map[string]string, error) {
	var rows []struct {
		UserID        string `db:"user_id"`
		AssociationID string `db:"sport_association_id"`
	}
	err := t.tx.SelectContext(ctx, &rows, `SELECT user_id,sport_association_id FROM application_sportassociation WHERE NOT deleted AND (user_id=$1 OR user_id=$2)`, target, actor)
	result := map[string]string{}
	for _, row := range rows {
		result[row.UserID] = row.AssociationID
	}
	return result, err
}
func (t *loginPreparationTransaction) AppendUsernameAudit(ctx context.Context, event domain.UsernameAudit) error {
	return credentialTransactionError(appendModelAudit(ctx, t.tx, modelAuditEntry{
		Model: "user", Key: event.AccountID, Representation: event.ObjectRepresentation, Action: 1,
		Changes: map[string][2]string{"username": {event.Before, event.After}}, Context: event.Context,
		AssociationID: event.AssociationID, ResolutionPath: event.ResolutionPath, At: event.At,
	}))
}
func (t *loginPreparationTransaction) Commit() error {
	return credentialTransactionError(t.ownedTransaction.Commit())
}
func (t *loginPreparationTransaction) Rollback() error { return t.ownedTransaction.Rollback() }
