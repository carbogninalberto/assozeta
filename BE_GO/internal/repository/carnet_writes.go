package repository

import (
	"context"
	"database/sql"
	"errors"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type CarnetWrites struct{ db *sqlx.DB }

func NewCarnetWrites(db *sqlx.DB) *CarnetWrites { return &CarnetWrites{db: db} }

var _ domain.CarnetDeleteStore = (*CarnetWrites)(nil)

func (r *CarnetWrites) BeginCarnetWrite(ctx context.Context) (domain.CarnetWriteTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &carnetWriteTransaction{dashboardLayoutTransaction: &dashboardLayoutTransaction{
		paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}}, nil
}

type carnetWriteTransaction struct{ *dashboardLayoutTransaction }

func (t *carnetWriteTransaction) OwnedCarnetForUpdate(ctx context.Context, id, owner string, scope *string) (string, error) {
	var association string
	err := t.tx.GetContext(ctx, &association, `SELECT c.sport_association_id FROM application_carnet c
 JOIN application_sportassociation sa ON sa.sport_association_id=c.sport_association_id
 WHERE c.carnet_id=$1 AND sa.user_id=$2 AND ($3::uuid IS NULL OR c.sport_association_id=$3::uuid) FOR UPDATE OF c`, id, owner, scope)
	if errors.Is(err, sql.ErrNoRows) {
		return "", domain.ErrCarnetNotFound
	}
	return association, err
}

// DeleteCarnet mirrors carnet.delete(): the collector removes the course links
// (no audit), each carnet subscription and then the carnet, logging every
// deleted model row. Deleted rows no longer resolve, so subscriptions (no
// association field) fall back to the actor's association and the carnet to
// its changes.
func (t *carnetWriteTransaction) DeleteCarnet(ctx context.Context, id, association string, actorAssociation *string, audit domain.LoginAuditContext, at time.Time) error {
	holdings := []string{}
	if err := t.tx.SelectContext(ctx, &holdings, `SELECT carnet_subscription_id FROM application_carnetsubscription
 WHERE carnet_id_id=$1 ORDER BY carnet_subscription_id FOR UPDATE`, id); err != nil {
		return err
	}
	if _, err := t.tx.ExecContext(ctx, `DELETE FROM application_carnetsubscription_course_subscription
 WHERE carnetsubscription_id=ANY($1::uuid[])`, holdings); err != nil {
		return err
	}
	actorPath := "actor.sport_association"
	for _, holding := range holdings {
		changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_carnetsubscription", "carnet_subscription_id", holding)
		if err != nil {
			return err
		}
		entry := modelAuditEntry{Model: "carnetsubscription", Key: holding, Representation: "CarnetSubscription object (" + holding + ")",
			Action: 2, Changes: changes, Context: audit, At: at}
		if actorAssociation != nil {
			entry.AssociationID, entry.ResolutionPath = actorAssociation, &actorPath
		}
		if err = appendModelAudit(ctx, t.tx, entry); err != nil {
			return err
		}
	}
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_carnet", "carnet_id", id)
	if err != nil {
		return err
	}
	path := "changes_json.sport_association"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "carnet", Key: id, Representation: "Carnet object (" + id + ")",
		Action: 2, Changes: changes, Context: audit, AssociationID: &association, ResolutionPath: &path, At: at})
}

var _ domain.CarnetUpdateStore = (*CarnetWrites)(nil)

func (r *CarnetWrites) BeginCarnetUpdate(ctx context.Context) (domain.CarnetUpdateTransaction, error) {
	tx, err := r.BeginCarnetWrite(ctx)
	if err != nil {
		return nil, err
	}
	return tx.(*carnetWriteTransaction), nil
}

func (t *carnetWriteTransaction) CarnetForEdit(ctx context.Context, id, owner string, scope *string) (domain.CarnetEditable, error) {
	var row domain.CarnetEditable
	err := t.tx.GetContext(ctx, &row, `SELECT c.sport_association_id, c.title, c.description, c.lessons_number, c.fee::text AS fee, c.public
 FROM application_carnet c JOIN application_sportassociation sa ON sa.sport_association_id=c.sport_association_id
 WHERE c.carnet_id=$1 AND sa.user_id=$2 AND ($3::uuid IS NULL OR c.sport_association_id=$3::uuid) FOR UPDATE OF c`, id, owner, scope)
	if errors.Is(err, sql.ErrNoRows) {
		return row, domain.ErrCarnetNotFound
	}
	return row, err
}

// UpdateCarnet writes the serializer's fields and, when auditlog would log
// changes, one update entry indexed directly to the carnet's association.
func (t *carnetWriteTransaction) UpdateCarnet(ctx context.Context, write domain.CarnetUpdateWrite, association string) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_carnet SET title=$2, description=$3, fee=$4::numeric, lessons_number=$5, public=$6
 WHERE carnet_id=$1`, write.ID, write.Title, write.Description, write.Fee, write.Lessons, write.Public); err != nil {
		return err
	}
	if len(write.Changes) == 0 {
		return nil
	}
	path := "direct"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "carnet", Key: write.ID, Representation: "Carnet object (" + write.ID + ")",
		Action: 1, Changes: write.Changes, Context: write.Context, AssociationID: &association, ResolutionPath: &path, At: write.At})
}

var _ domain.CarnetUnassignStore = (*CarnetWrites)(nil)

func (r *CarnetWrites) BeginCarnetUnassign(ctx context.Context) (domain.CarnetUnassignTransaction, error) {
	tx, err := r.BeginCarnetWrite(ctx)
	if err != nil {
		return nil, err
	}
	return tx.(*carnetWriteTransaction), nil
}

func (t *carnetWriteTransaction) HoldingForUnassign(ctx context.Context, holding, owner string, scope *string) (domain.CarnetHoldingPayment, error) {
	var row domain.CarnetHoldingPayment
	err := t.tx.GetContext(ctx, &row, `SELECT h.payment_id, p.paid FROM application_carnetsubscription h
 JOIN application_subscription s ON s.subscription_id=h.subscription_id
 JOIN application_sportassociation sa ON sa.sport_association_id=s.sport_association_id
 LEFT JOIN application_carnet c ON c.carnet_id=h.carnet_id_id
 LEFT JOIN application_payment p ON p.payment_id=h.payment_id
 WHERE h.carnet_subscription_id=$1 AND sa.user_id=$2
  AND ($3::uuid IS NULL OR (s.sport_association_id=$3::uuid AND c.sport_association_id=$3::uuid))
 ORDER BY h.carnet_subscription_id LIMIT 1 FOR UPDATE OF h`, holding, owner, scope)
	if errors.Is(err, sql.ErrNoRows) {
		return row, domain.ErrCarnetNotFound
	}
	return row, err
}

// paymentThroughTables are Payment's many-to-many join tables: the collector
// deletes their rows. Every other foreign key to a payment is SET_NULL.
var paymentThroughTables = map[string]bool{"application_payment_attachments": true, "application_coursesubscription_membership_payments": true}

// DeleteUnpaidPayment mirrors payment.delete(): installments of the payment
// would be deleted by a pre_delete signal (refused, not ported); SET_NULL
// references are cleared and join rows removed without audit; the payment
// gets a delete entry indexed through its changes.
func (t *carnetWriteTransaction) DeleteUnpaidPayment(ctx context.Context, payment string, audit domain.LoginAuditContext, at time.Time) error {
	var installments bool
	if err := t.tx.GetContext(ctx, &installments, `SELECT EXISTS(SELECT 1 FROM application_coursesubscriptioninstallment WHERE payment_id=$1)`, payment); err != nil {
		return err
	}
	if installments {
		return domain.ErrPaymentCascadeUnsupported
	}
	refs := []struct {
		Table  string `db:"table_name"`
		Column string `db:"column_name"`
	}{}
	if err := t.tx.SelectContext(ctx, &refs, `SELECT kcu.table_name, kcu.column_name FROM information_schema.referential_constraints rc
 JOIN information_schema.key_column_usage kcu ON kcu.constraint_name=rc.constraint_name AND kcu.constraint_schema=rc.constraint_schema
 JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name=rc.unique_constraint_name AND ccu.constraint_schema=rc.unique_constraint_schema
 WHERE ccu.table_name='application_payment' AND ccu.column_name='payment_id' AND kcu.table_schema=current_schema()
 ORDER BY kcu.table_name, kcu.column_name`); err != nil {
		return err
	}
	for _, ref := range refs {
		statement := `UPDATE ` + pq(ref.Table) + ` SET ` + pq(ref.Column) + `=NULL WHERE ` + pq(ref.Column) + `=$1`
		if paymentThroughTables[ref.Table] {
			statement = `DELETE FROM ` + pq(ref.Table) + ` WHERE ` + pq(ref.Column) + `=$1`
		}
		if _, err := t.tx.ExecContext(ctx, statement, payment); err != nil {
			return err
		}
	}
	// The reverse many-to-many accessor is not rendered on delete.
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_payment", "payment_id", payment)
	if err != nil {
		return err
	}
	association := changes["sport_association"][0]
	path := "changes_json.sport_association"
	entry := modelAuditEntry{Model: "payment", Key: payment, Representation: "Payment object (" + payment + ")", Action: 2,
		Changes: changes, Context: audit, At: at}
	if association != "" && association != "None" {
		entry.AssociationID, entry.ResolutionPath = &association, &path
	}
	return appendModelAudit(ctx, t.tx, entry)
}

func pq(identifier string) string { return `"` + strings.ReplaceAll(identifier, `"`, `""`) + `"` }

// DeleteHolding mirrors carnet_subscription.delete() after its unpaid payment
// was cleared in memory: course links go without audit, the row gets a delete
// entry indexed through the actor's association.
func (t *carnetWriteTransaction) DeleteHolding(ctx context.Context, holding string, actorAssociation *string, audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `DELETE FROM application_carnetsubscription_course_subscription WHERE carnetsubscription_id=$1`, holding); err != nil {
		return err
	}
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_carnetsubscription", "carnet_subscription_id", holding)
	if err != nil {
		return err
	}
	entry := modelAuditEntry{Model: "carnetsubscription", Key: holding, Representation: "CarnetSubscription object (" + holding + ")",
		Action: 2, Changes: changes, Context: audit, At: at}
	if actorAssociation != nil {
		path := "actor.sport_association"
		entry.AssociationID, entry.ResolutionPath = actorAssociation, &path
	}
	return appendModelAudit(ctx, t.tx, entry)
}

var _ domain.CarnetToggleStore = (*CarnetWrites)(nil)

func (r *CarnetWrites) BeginCarnetToggle(ctx context.Context) (domain.CarnetToggleTransaction, error) {
	tx, err := r.BeginCarnetWrite(ctx)
	if err != nil {
		return nil, err
	}
	return tx.(*carnetWriteTransaction), nil
}

func (t *carnetWriteTransaction) HoldingForToggle(ctx context.Context, holding, owner string, scope *string) (bool, string, error) {
	var row struct {
		Disabled    bool   `db:"disabled"`
		Association string `db:"sport_association_id"`
	}
	err := t.tx.GetContext(ctx, &row, `SELECT h.disabled, s.sport_association_id FROM application_carnetsubscription h
 JOIN application_subscription s ON s.subscription_id=h.subscription_id
 JOIN application_sportassociation sa ON sa.sport_association_id=s.sport_association_id
 LEFT JOIN application_carnet c ON c.carnet_id=h.carnet_id_id
 WHERE h.carnet_subscription_id=$1 AND sa.user_id=$2
  AND ($3::uuid IS NULL OR (s.sport_association_id=$3::uuid AND c.sport_association_id=$3::uuid)) FOR UPDATE OF h`, holding, owner, scope)
	if errors.Is(err, sql.ErrNoRows) {
		return false, "", domain.ErrCarnetSubscriptionNotFound
	}
	return row.Disabled, row.Association, err
}

// SetHoldingDisabled saves the flag; a change is audited and indexed through
// the subscription's association (carnet_subscription_resolver).
func (t *carnetWriteTransaction) SetHoldingDisabled(ctx context.Context, holding string, disabled bool, association string, audit domain.LoginAuditContext, at time.Time, changed bool) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_carnetsubscription SET disabled=$2 WHERE carnet_subscription_id=$1`, holding, disabled); err != nil {
		return err
	}
	if !changed {
		return nil
	}
	old, updated := "False", "True"
	if !disabled {
		old, updated = "True", "False"
	}
	path := "subscription.sport_association"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "carnetsubscription", Key: holding,
		Representation: "CarnetSubscription object (" + holding + ")", Action: 1, Changes: map[string][2]string{"disabled": {old, updated}},
		Context: audit, AssociationID: &association, ResolutionPath: &path, At: at})
}
