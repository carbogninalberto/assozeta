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

var _ domain.CarnetLinkStore = (*CarnetWrites)(nil)

func (r *CarnetWrites) BeginCarnetLink(ctx context.Context) (domain.CarnetLinkTransaction, error) {
	tx, err := r.BeginCarnetWrite(ctx)
	if err != nil {
		return nil, err
	}
	return tx.(*carnetWriteTransaction), nil
}

func (t *carnetWriteTransaction) RemoveCourseLink(ctx context.Context, holding, course string) error {
	_, err := t.tx.ExecContext(ctx, `DELETE FROM application_carnetsubscription_course_subscription
 WHERE carnetsubscription_id=$1 AND coursesubscription_id IN
  (SELECT course_subscription_id FROM application_coursesubscription WHERE course_subscription_id=$2 AND NOT deleted)`, holding, course)
	return err
}

var _ domain.CarnetMetaStore = (*CarnetWrites)(nil)

func (r *CarnetWrites) BeginCarnetMeta(ctx context.Context) (domain.CarnetMetaTransaction, error) {
	tx, err := r.BeginCarnetWrite(ctx)
	if err != nil {
		return nil, err
	}
	return tx.(*carnetWriteTransaction), nil
}

func (t *carnetWriteTransaction) HoldingMeta(ctx context.Context, holding, owner string, scope *string) (*string, string, error) {
	var row struct {
		Meta        *string `db:"meta"`
		Association string  `db:"sport_association_id"`
	}
	err := t.tx.GetContext(ctx, &row, `SELECT h.meta::text AS meta, s.sport_association_id FROM application_carnetsubscription h
 JOIN application_subscription s ON s.subscription_id=h.subscription_id
 JOIN application_sportassociation sa ON sa.sport_association_id=s.sport_association_id
 LEFT JOIN application_carnet c ON c.carnet_id=h.carnet_id_id
 WHERE h.carnet_subscription_id=$1 AND sa.user_id=$2
  AND ($3::uuid IS NULL OR (s.sport_association_id=$3::uuid AND c.sport_association_id=$3::uuid)) FOR UPDATE OF h`, holding, owner, scope)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, "", domain.ErrCarnetSubscriptionNotFound
	}
	return row.Meta, row.Association, err
}

func (t *carnetWriteTransaction) SaveHoldingMeta(ctx context.Context, holding, meta, association string, changes map[string][2]string, audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_carnetsubscription SET meta=$2::jsonb WHERE carnet_subscription_id=$1`, holding, meta); err != nil {
		return err
	}
	if len(changes) == 0 {
		return nil
	}
	path := "subscription.sport_association"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "carnetsubscription", Key: holding,
		Representation: "CarnetSubscription object (" + holding + ")", Action: 1, Changes: changes, Context: audit,
		AssociationID: &association, ResolutionPath: &path, At: at})
}

func (r *CarnetWrites) BeginCarnetTopup(ctx context.Context) (domain.CarnetTopupTransaction, error) {
	transaction, err := r.BeginCarnetWrite(ctx)
	if err != nil {
		return nil, err
	}
	return transaction.(*carnetWriteTransaction), nil
}

func (t *carnetWriteTransaction) TopupSource(ctx context.Context, holding string, owner *string) (domain.CarnetTopupSource, error) {
	var source domain.CarnetTopupSource
	err := t.tx.GetContext(ctx, &source, `SELECT h.user_id_id AS user_id, c.carnet_id, c.sport_association_id AS carnet_association,
 c.fee::text AS fee, c.lessons_number, s.subscription_id, s.user_id AS subscription_user, s.associate_id AS subscription_associate,
 s.sport_association_id AS subscription_association
 FROM application_carnetsubscription h JOIN application_carnet c ON c.carnet_id=h.carnet_id_id
 LEFT JOIN application_subscription s ON s.subscription_id=h.subscription_id
 LEFT JOIN application_sportassociation a ON a.sport_association_id=s.sport_association_id
 WHERE h.carnet_subscription_id=$1 AND ($2::uuid IS NULL OR a.user_id=$2)`, holding, owner)
	if errors.Is(err, sql.ErrNoRows) {
		return source, domain.ErrCarnetSubscriptionNotFound
	}
	return source, err
}

func (t *carnetWriteTransaction) DefaultPaymentCategory(ctx context.Context) (*string, *string, error) {
	return (&paymentArchiveTransaction{paymentCategoryTransaction: t.paymentCategoryTransaction}).DefaultPaymentCategory(ctx)
}

func (t *carnetWriteTransaction) DefaultBankAccount(ctx context.Context, association *string) (*string, error) {
	var id string
	// CustomAccounts has no ordering: first() orders by the primary key.
	err := t.tx.GetContext(ctx, &id, `SELECT custom_account_id FROM application_customaccounts
 WHERE sport_association_id IS NOT DISTINCT FROM $1::uuid AND NOT editable AND enabled AND account_type=2
 ORDER BY custom_account_id LIMIT 1`, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &id, nil
}

// CreateTopup mirrors Payment.objects.create then CarnetSubscription.objects.create:
// one create entry each, rendered from the stored row; the repeated save()
// calls and course_subscription.add() record nothing.
func (t *carnetWriteTransaction) CreateTopup(ctx context.Context, write domain.CarnetTopupWrite) error {
	from := write.Source
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_payment (payment_id,type,subject,amount,creation_date,paid,expense,
 description,payment_category_id,user_id,associate_id,sport_association_id,archived,custom_accounts_id,deleted,group_id)
 VALUES ($1,'default',2,$2::numeric,$3,false,false,'Quota Corso',$4,$5,$6,$7,false,$8,false,$9)`,
		write.PaymentID, from.Fee, write.At, write.CategoryID, from.SubscriptionUser, from.SubscriptionAssociate, from.CarnetAssociation, write.Account,
		write.Group); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_payment", "payment_id", write.PaymentID)
	if err != nil {
		return err
	}
	entry := modelAuditEntry{Model: "payment", Key: write.PaymentID, Representation: "Payment object (" + write.PaymentID + ")", Action: 0,
		Changes: changes, Context: write.Context, At: write.At}
	direct := "direct"
	if from.CarnetAssociation != nil {
		entry.AssociationID, entry.ResolutionPath = from.CarnetAssociation, &direct
	}
	if err = appendModelAudit(ctx, t.tx, entry); err != nil {
		return err
	}
	if _, err = t.tx.ExecContext(ctx, `INSERT INTO application_carnetsubscription
 (carnet_subscription_id,user_id_id,subscription_id,carnet_id_id,creation_date,payment_id,disabled,meta)
 VALUES ($1,$2,$3,$4,$5,$6,false,$7::jsonb)`, write.HoldingID, from.UserID, from.SubscriptionID, from.CarnetID, write.At, write.PaymentID, write.Meta); err != nil {
		return err
	}
	if changes, err = createdModelChanges(ctx, t.tx, "application_carnetsubscription", "carnet_subscription_id", write.HoldingID); err != nil {
		return err
	}
	entry = modelAuditEntry{Model: "carnetsubscription", Key: write.HoldingID, Representation: "CarnetSubscription object (" + write.HoldingID + ")",
		Action: 0, Changes: changes, Context: write.Context, At: write.At}
	path := "subscription.sport_association"
	if from.SubscriptionAssociation != nil {
		entry.AssociationID, entry.ResolutionPath = from.SubscriptionAssociation, &path
	}
	if err = appendModelAudit(ctx, t.tx, entry); err != nil {
		return err
	}
	if write.LinkFrom != nil {
		// old_carnet.course_subscription.all() uses the soft-delete manager.
		if _, err = t.tx.ExecContext(ctx, `INSERT INTO application_carnetsubscription_course_subscription (carnetsubscription_id,coursesubscription_id)
 SELECT $1, l.coursesubscription_id FROM application_carnetsubscription_course_subscription l
 JOIN application_coursesubscription cs ON cs.course_subscription_id=l.coursesubscription_id
 WHERE l.carnetsubscription_id=$2 AND NOT cs.deleted`, write.HoldingID, *write.LinkFrom); err != nil {
			return err
		}
	}
	if write.Link != nil {
		return t.LinkCourse(ctx, write.HoldingID, *write.Link)
	}
	return nil
}

func (r *CarnetWrites) BeginCarnetAssign(ctx context.Context) (domain.CarnetAssignTransaction, error) {
	transaction, err := r.BeginCarnetWrite(ctx)
	if err != nil {
		return nil, err
	}
	return transaction.(*carnetWriteTransaction), nil
}

func carnetAssignFirst[T any](ctx context.Context, tx *sqlx.Tx, query string, args ...any) (*T, error) {
	var row T
	err := tx.GetContext(ctx, &row, query, args...)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *carnetWriteTransaction) AssignSubscription(ctx context.Context, id string, group *string) (*domain.CarnetAssignSubscription, error) {
	return carnetAssignFirst[domain.CarnetAssignSubscription](ctx, t.tx, `SELECT subscription_id, user_id, associate_id, sport_association_id
 FROM application_subscription WHERE subscription_id=$1 AND ($2::uuid IS NULL OR group_id=$2::uuid)`, id, group)
}

func (t *carnetWriteTransaction) OwnerAssociation(ctx context.Context, owner string, scope *string) (*string, error) {
	return carnetAssignFirst[string](ctx, t.tx, `SELECT sport_association_id FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR sport_association_id=$2::uuid) ORDER BY sport_association_id LIMIT 1`, owner, scope)
}

func (t *carnetWriteTransaction) AssociationCarnet(ctx context.Context, id, association string) (*domain.CarnetAssignCarnet, error) {
	return carnetAssignFirst[domain.CarnetAssignCarnet](ctx, t.tx, `SELECT carnet_id, fee::text AS fee, lessons_number FROM application_carnet
 WHERE carnet_id=$1 AND sport_association_id=$2`, id, association)
}

func (t *carnetWriteTransaction) AssociationCourseSubscription(ctx context.Context, id, association string) (*string, error) {
	return carnetAssignFirst[string](ctx, t.tx, `SELECT cs.course_subscription_id FROM application_coursesubscription cs
 JOIN application_course c ON c.course_id=cs.course_id JOIN application_subscription s ON s.subscription_id=cs.subscription_id
 WHERE cs.course_subscription_id=$1 AND NOT cs.deleted AND c.sport_association_id=$2 AND s.sport_association_id=$2`, id, association)
}

func (t *carnetWriteTransaction) AssignHolding(ctx context.Context, id, association string, holder *string) (bool, error) {
	var found bool
	err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_carnetsubscription h
 JOIN application_subscription s ON s.subscription_id=h.subscription_id
 WHERE h.carnet_subscription_id=$1 AND s.sport_association_id=$2 AND ($3::uuid IS NULL OR s.user_id=$3::uuid))`, id, association, holder)
	return found, err
}

func (t *carnetWriteTransaction) SubscriptionCourse(ctx context.Context, course *string, group *string, subscription string) (*string, error) {
	// Course.objects is live and group-aware; filter(course=None) is IS NULL.
	return carnetAssignFirst[string](ctx, t.tx, `SELECT cs.course_subscription_id FROM application_coursesubscription cs
 WHERE NOT cs.deleted AND cs.subscription_id=$3 AND cs.course_id IS NOT DISTINCT FROM (
  SELECT c.course_id FROM application_course c WHERE c.course_id=$1::uuid AND NOT c.deleted AND ($2::uuid IS NULL OR c.group_id=$2::uuid)
  ORDER BY c.course_id LIMIT 1)
 ORDER BY cs.course_subscription_id LIMIT 1`, course, group, subscription)
}

// LinkCourse is course_subscription.add(): an existing link is kept.
func (t *carnetWriteTransaction) LinkCourse(ctx context.Context, holding, course string) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_carnetsubscription_course_subscription (carnetsubscription_id,coursesubscription_id)
 SELECT $1,$2 WHERE NOT EXISTS (SELECT 1 FROM application_carnetsubscription_course_subscription
  WHERE carnetsubscription_id=$1 AND coursesubscription_id=$2)`, holding, course)
	return err
}
