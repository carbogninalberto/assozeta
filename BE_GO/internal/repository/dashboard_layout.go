package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type DashboardLayouts struct{ db *sqlx.DB }

func NewDashboardLayouts(db *sqlx.DB) *DashboardLayouts { return &DashboardLayouts{db: db} }

var _ domain.DashboardLayoutStore = (*DashboardLayouts)(nil)

func (r *DashboardLayouts) BeginDashboardLayout(ctx context.Context) (domain.DashboardLayoutTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &dashboardLayoutTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

type dashboardLayoutTransaction struct{ *paymentCategoryTransaction }

var _ domain.DashboardLayoutTransaction = (*dashboardLayoutTransaction)(nil)

func (t *dashboardLayoutTransaction) ActiveAssociationOf(ctx context.Context, user string) (*string, error) {
	var id string
	err := t.tx.GetContext(ctx, &id, `SELECT sport_association_id FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted ORDER BY sport_association_id LIMIT 1`, user)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &id, err
}

func (t *dashboardLayoutTransaction) DashboardLayoutForUpdate(ctx context.Context, user string) (domain.DashboardLayoutOwner, error) {
	var owner domain.DashboardLayoutOwner
	err := t.tx.GetContext(ctx, &owner, `SELECT first_name,last_name,dashboard_layout::text AS dashboard_layout
 FROM bakney_user WHERE user_id=$1 FOR UPDATE`, user)
	return owner, err
}

func (t *dashboardLayoutTransaction) AppendDashboardLayoutAudit(ctx context.Context, event domain.DashboardLayoutAudit) error {
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "user", Key: event.UserID, Representation: event.Representation,
		Action: 1, Changes: event.Changes, Context: event.Context, AssociationID: event.AssociationID,
		ResolutionPath: event.ResolutionPath, At: event.At})
}

// SetDashboardLayout isolates the update so a rejected jsonb value leaves the
// already written audit entry committable, like auditlog's pre_save entry.
func (t *dashboardLayoutTransaction) SetDashboardLayout(ctx context.Context, user string, layout *string) error {
	if _, err := t.tx.ExecContext(ctx, `SAVEPOINT dashboard_layout`); err != nil {
		return err
	}
	_, err := t.tx.ExecContext(ctx, `UPDATE bakney_user SET dashboard_layout=$2::jsonb WHERE user_id=$1`, user, layout)
	if err != nil {
		if _, rollback := t.tx.ExecContext(ctx, `ROLLBACK TO SAVEPOINT dashboard_layout`); rollback != nil {
			return errors.Join(err, rollback)
		}
		return &domain.DashboardLayoutRejected{Err: err}
	}
	return nil
}
