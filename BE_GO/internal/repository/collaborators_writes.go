package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type CollaboratorUpdates struct{ db *sqlx.DB }

func NewCollaboratorUpdates(db *sqlx.DB) *CollaboratorUpdates { return &CollaboratorUpdates{db: db} }

var _ domain.CollaboratorUpdateStore = (*CollaboratorUpdates)(nil)

func (r *CollaboratorUpdates) BeginCollaboratorUpdate(ctx context.Context) (domain.CollaboratorUpdateTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &collaboratorUpdateTransaction{dashboardLayoutTransaction: &dashboardLayoutTransaction{
		paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}}, nil
}

// Reuses the owned transaction and the active-association-of-user lookup.
type collaboratorUpdateTransaction struct{ *dashboardLayoutTransaction }

var _ domain.CollaboratorUpdateTransaction = (*collaboratorUpdateTransaction)(nil)

func (t *collaboratorUpdateTransaction) ConnectedCollaboratorForUpdate(ctx context.Context, owner, id string) (*domain.CollaboratorUpdateTarget, error) {
	var row domain.CollaboratorUpdateTarget
	err := t.tx.GetContext(ctx, &row, `SELECT first_name,last_name,collaborator_role,collaborator_permissions::text AS collaborator_permissions
 FROM bakney_user WHERE connected_user_id=$1 AND user_id=$2 AND NOT deleted FOR UPDATE`, owner, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *collaboratorUpdateTransaction) AppendCollaboratorAudit(ctx context.Context, event domain.DashboardLayoutAudit) error {
	return t.AppendDashboardLayoutAudit(ctx, event)
}

// SetCollaboratorRole isolates the update so a rejected jsonb value leaves the
// already written audit entry committable, like auditlog's pre_save entry.
func (t *collaboratorUpdateTransaction) SetCollaboratorRole(ctx context.Context, user string, role int64, permissions *string) error {
	if _, err := t.tx.ExecContext(ctx, `SAVEPOINT collaborator_update`); err != nil {
		return err
	}
	_, err := t.tx.ExecContext(ctx, `UPDATE bakney_user SET collaborator_role=$2,collaborator_permissions=$3::jsonb WHERE user_id=$1`,
		user, role, permissions)
	if err != nil {
		if _, rollback := t.tx.ExecContext(ctx, `ROLLBACK TO SAVEPOINT collaborator_update`); rollback != nil {
			return errors.Join(err, rollback)
		}
		return &domain.DashboardLayoutRejected{Err: err}
	}
	return nil
}
