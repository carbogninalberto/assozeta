package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type AssociationAdmin struct{ db *sqlx.DB }

func NewAssociationAdmin(db *sqlx.DB) *AssociationAdmin { return &AssociationAdmin{db: db} }

var _ domain.AssociationAdminStore = (*AssociationAdmin)(nil)

func (r *AssociationAdmin) BeginAssociationAdmin(ctx context.Context) (domain.AssociationAdminTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	// Django's autocommit saves check deferred foreign keys per statement.
	if _, err = tx.tx.ExecContext(ctx, `SET CONSTRAINTS ALL IMMEDIATE`); err != nil {
		_ = tx.Rollback()
		return nil, err
	}
	return &associationAdminTransaction{dashboardLayoutTransaction: &dashboardLayoutTransaction{
		paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}}, nil
}

// Reuses the owned transaction and the active-association-of-user lookup.
type associationAdminTransaction struct{ *dashboardLayoutTransaction }

var _ domain.AssociationAdminTransaction = (*associationAdminTransaction)(nil)

func (t *associationAdminTransaction) AdminAssociation(ctx context.Context, id string, scope *string) (string, *string, error) {
	var row struct {
		Owner string  `db:"user_id"`
		Notes *string `db:"notes"`
	}
	err := t.tx.GetContext(ctx, &row, `SELECT user_id,notes FROM application_sportassociation
 WHERE sport_association_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR sport_association_id=$2::uuid) FOR UPDATE`, id, scope)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil, domain.ErrAdminUpdateNotFound
	}
	return row.Owner, row.Notes, err
}

func (t *associationAdminTransaction) FirstBillingForUpdate(ctx context.Context, user string) (domain.AdminBilling, error) {
	var row domain.AdminBilling
	err := t.tx.GetContext(ctx, &row, `SELECT billing_subscription_id,auto_renewal,renewal_type,ends_on,billing_plan_id
 FROM billing_subscription WHERE user_id=$1 ORDER BY billing_subscription_id LIMIT 1 FOR UPDATE`, user)
	if errors.Is(err, sql.ErrNoRows) {
		return row, domain.ErrAdminUpdateNotFound
	}
	return row, err
}

func (t *associationAdminTransaction) step(ctx context.Context, run func() error) error {
	if _, err := t.tx.ExecContext(ctx, `SAVEPOINT admin_update_step`); err != nil {
		return err
	}
	if err := run(); err != nil {
		if _, rollback := t.tx.ExecContext(ctx, `ROLLBACK TO SAVEPOINT admin_update_step`); rollback != nil {
			return errors.Join(err, rollback)
		}
		return err
	}
	_, err := t.tx.ExecContext(ctx, `RELEASE SAVEPOINT admin_update_step`)
	return err
}

func (t *associationAdminTransaction) AppendAdminAudit(ctx context.Context, event domain.AdminAudit) error {
	return t.step(ctx, func() error {
		return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: event.Model, Key: event.ObjectID, Representation: event.Representation,
			Action: 1, Changes: event.Changes, Context: event.Context, AssociationID: event.AssociationID,
			ResolutionPath: event.ResolutionPath, At: event.At})
	})
}

func (t *associationAdminTransaction) UpdateBilling(ctx context.Context, id string, write domain.AdminBillingWrite) error {
	return t.step(ctx, func() error {
		_, err := t.tx.ExecContext(ctx, `UPDATE billing_subscription SET auto_renewal=$2,renewal_type=$3,ends_on=$4,billing_plan_id=$5
 WHERE billing_subscription_id=$1`, id, write.AutoRenewal, write.RenewalType, write.EndsOn, write.BillingPlan)
		return err
	})
}

func (t *associationAdminTransaction) UpdateNotes(ctx context.Context, association string, notes *string) error {
	return t.step(ctx, func() error {
		_, err := t.tx.ExecContext(ctx, `UPDATE application_sportassociation SET notes=$2 WHERE sport_association_id=$1`, association, notes)
		return err
	})
}
