package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// SubscriptionLifecycles backs subscription/<uid>/reject, archive and delete.
// It shares the course subscription collector (payment, installment and
// course subscription deletes with their pre_delete signals) and the
// payment archive save.
type SubscriptionLifecycles struct{ db *sqlx.DB }

func NewSubscriptionLifecycles(db *sqlx.DB) *SubscriptionLifecycles {
	return &SubscriptionLifecycles{db: db}
}

var _ domain.SubscriptionLifecycleStore = (*SubscriptionLifecycles)(nil)

func (r *SubscriptionLifecycles) BeginSubscriptionLifecycle(ctx context.Context) (domain.SubscriptionLifecycleTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	shared := &paymentCategoryTransaction{ownedTransaction: tx}
	return &subscriptionLifecycleTransaction{courseSubscriptionTransaction: &courseSubscriptionTransaction{paymentCategoryTransaction: shared}}, nil
}

type subscriptionLifecycleTransaction struct{ *courseSubscriptionTransaction }

func (t *subscriptionLifecycleTransaction) LifecycleAssociation(ctx context.Context, owner string, scope *string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, owner, scope, false)
}

func (t *subscriptionLifecycleTransaction) PaymentSaves() domain.PaymentArchiveTransaction {
	return &paymentArchiveTransaction{paymentCategoryTransaction: t.paymentCategoryTransaction}
}

func (t *subscriptionLifecycleTransaction) Subscription(ctx context.Context, id string, scope, group *string) (*domain.SubscriptionLifecycleRow, error) {
	var row domain.SubscriptionLifecycleRow
	err := t.tx.GetContext(ctx, &row, `SELECT s.subscription_id, s.sport_association_id, s.associate_id, s.status_flag, s.acceptance_date,
 s.start_date, s.end_date, s.creation_date, s.archived, s.payment_id, s.signature_storage_key, u.subscription_start_day, u.subscription_start_month,
 u.custom_end_date, u.subscription_end_day, u.subscription_end_month, u.subscription_duration
 FROM application_subscription s JOIN application_sportassociation a ON a.sport_association_id=s.sport_association_id
 JOIN bakney_user u ON u.user_id=a.user_id
 WHERE s.subscription_id=$1 AND ($2::uuid IS NULL OR s.sport_association_id=$2::uuid) AND ($3::uuid IS NULL OR s.group_id=$3::uuid)
 FOR UPDATE OF s`, id, scope, group)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// SaveSubscription is Subscription.save(): the changed columns are written;
// the entry (if any) is indexed directly to the subscription's association.
func (t *subscriptionLifecycleTransaction) SaveSubscription(ctx context.Context, save domain.SubscriptionLifecycleSave, audit domain.CourseSubscriptionAudit) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_subscription SET status_flag=$2, acceptance_date=$3::date, start_date=$4::date,
 end_date=$5::date, archived=$6, payment_id=$7 WHERE subscription_id=$1`, save.ID, save.Status, save.Acceptance, save.Start, save.End, save.Archived,
		save.PaymentID); err != nil {
		return err
	}
	if len(save.Changes) == 0 {
		return nil
	}
	association := save.AssociationID
	return appendModelAudit(ctx, t.tx, courseSubscriptionEntry("subscription", "Subscription", save.ID, "", 1, save.Changes, &association, "direct", audit))
}

// Unordered Django querysets are read in the table's physical order (a
// sequential scan's order), which is the order Django iterates them.
func (t *subscriptionLifecycleTransaction) LiveCourseSubscriptions(ctx context.Context, subscription string, scope *string) ([]domain.CourseSubscriptionRow, error) {
	rows := []domain.CourseSubscriptionRow{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT `+courseSubscriptionColumns+` FROM application_coursesubscription cs
 JOIN application_course c ON c.course_id=cs.course_id
 WHERE cs.subscription_id=$1 AND NOT cs.deleted AND ($2::uuid IS NULL OR c.sport_association_id=$2::uuid)
 ORDER BY cs.ctid FOR UPDATE OF cs`, subscription, scope)
	return rows, err
}

func (t *subscriptionLifecycleTransaction) LiveInstallments(ctx context.Context, row string, group *string) ([]domain.SubscriptionLifecycleInstallment, error) {
	rows := []domain.SubscriptionLifecycleInstallment{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT course_subscription_installment_id, payment_id, archived FROM application_coursesubscriptioninstallment
 WHERE course_subscription_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid) ORDER BY ctid FOR UPDATE`, row, group)
	return rows, err
}

func (t *subscriptionLifecycleTransaction) Payment(ctx context.Context, id string) (*domain.SubscriptionLifecyclePayment, error) {
	var row domain.SubscriptionLifecyclePayment
	err := t.tx.GetContext(ctx, &row, `SELECT p.payment_id, COALESCE(p.sport_association_id::text, '') AS sport_association_id, p.subject, p.archived,
 p.payment_category_id, c.name AS payment_category_name, p.description, p.meta::text AS meta, p.course::text AS course, p.paid, p.invoice_id,
 p.deleted, p.group_id
 FROM application_payment p LEFT JOIN application_paymentcategory c ON c.payment_category_id=p.payment_category_id
 WHERE p.payment_id=$1 FOR UPDATE OF p`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *subscriptionLifecycleTransaction) ArchiveInstallment(ctx context.Context, installment string, association *string, audit domain.CourseSubscriptionAudit) error {
	var archived bool
	if err := t.tx.GetContext(ctx, &archived, `SELECT archived FROM application_coursesubscriptioninstallment
 WHERE course_subscription_installment_id=$1 FOR UPDATE`, installment); err != nil {
		return err
	}
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_coursesubscriptioninstallment SET archived=true
 WHERE course_subscription_installment_id=$1`, installment); err != nil {
		return err
	}
	if archived {
		return nil
	}
	return appendModelAudit(ctx, t.tx, courseSubscriptionEntry("coursesubscriptioninstallment", "CourseSubscriptionInstallment", installment, "", 1,
		map[string][2]string{"archived": {"False", "True"}}, association, "course_subscription.course.sport_association", audit))
}

func (t *subscriptionLifecycleTransaction) RangePayments(ctx context.Context, association string, associate *string, from, to time.Time,
	group *string) ([]string, error) {
	ids := []string{}
	err := t.tx.SelectContext(ctx, &ids, `SELECT payment_id FROM application_payment
 WHERE sport_association_id=$1 AND associate_id IS NOT DISTINCT FROM $2::uuid AND creation_date BETWEEN $3 AND $4
 AND NOT archived AND NOT paid AND NOT deleted AND ($5::uuid IS NULL OR group_id=$5::uuid) ORDER BY ctid FOR UPDATE`,
		association, associate, from, to, group)
	return ids, err
}

func (t *subscriptionLifecycleTransaction) DeletePayments(ctx context.Context, ids []string, audit domain.CourseSubscriptionAudit) error {
	payments := make([]*courseSubscriptionSnapshot, 0, len(ids))
	for _, id := range ids {
		snapshot, err := t.snapshot(ctx, "application_payment", "payment_id", id)
		if err != nil {
			return err
		}
		if snapshot == nil {
			return domain.ErrSubscriptionLifecycleNotPorted
		}
		payments = append(payments, snapshot)
	}
	if len(payments) == 0 {
		return nil
	}
	return t.deletePayments(ctx, payments, true, audit)
}

// DeleteLoopInstallment: Payment's pre_delete deletes the payment's live
// group-aware installments (this one included, nested P, I, P); the instance,
// its payment cleared in memory, is then deleted again: its own pre_delete
// has no payment and auditlog logs the in-memory row.
func (t *subscriptionLifecycleTransaction) DeleteLoopInstallment(ctx context.Context, installment domain.SubscriptionLifecycleInstallment,
	invoice *string, audit domain.CourseSubscriptionAudit) error {
	loaded, err := t.snapshot(ctx, "application_coursesubscriptioninstallment", "course_subscription_installment_id", installment.ID)
	if err != nil {
		return err
	}
	if loaded == nil {
		return domain.ErrSubscriptionLifecycleNotPorted
	}
	if invoice != nil {
		err = t.DeleteInvoicedPayment(ctx, *installment.PaymentID, *invoice, audit)
	} else {
		err = t.DeletePayments(ctx, []string{*installment.PaymentID}, audit)
	}
	if err != nil {
		return err
	}
	if _, err = t.tx.ExecContext(ctx, `DELETE FROM application_coursesubscriptioninstallment WHERE course_subscription_installment_id=$1`,
		installment.ID); err != nil {
		return err
	}
	changes := map[string][2]string{}
	for name, value := range loaded.changes {
		changes[name] = value
	}
	changes["payment"] = [2]string{"None", "None"}
	return appendModelAudit(ctx, t.tx, courseSubscriptionEntry("coursesubscriptioninstallment", "CourseSubscriptionInstallment", installment.ID, "", 2,
		changes, nil, "", audit))
}

func (t *subscriptionLifecycleTransaction) collectorRun(audit domain.CourseSubscriptionAudit) *collaboratorDeleteRun {
	return &collaboratorDeleteRun{tx: t.tx, graph: collaboratorDeleteGraphLoaded, audit: audit.Context, group: audit.Group,
		now: func() time.Time { return audit.At }}
}

// collectorDelete is Model.delete() of one row through Django's Collector.
func (t *subscriptionLifecycleTransaction) collectorDelete(ctx context.Context, label, id string, audit domain.CourseSubscriptionAudit) error {
	run := t.collectorRun(audit)
	model := collaboratorDeleteGraphLoaded.Models[label]
	rows, err := run.load(ctx, model, `t.`+model.PK+` = $1::uuid`, id)
	if err != nil {
		return err
	}
	if len(rows) == 0 {
		return domain.ErrSubscriptionLifecycleNotPorted
	}
	return run.Delete(ctx, label, rows)
}

func (t *subscriptionLifecycleTransaction) DeleteInvoice(ctx context.Context, invoice string, audit domain.CourseSubscriptionAudit) error {
	return t.collectorDelete(ctx, "application.Invoice", invoice, audit)
}

func (t *subscriptionLifecycleTransaction) DeleteInvoicedPayment(ctx context.Context, payment, invoice string, audit domain.CourseSubscriptionAudit) error {
	snapshot, err := t.snapshot(ctx, "application_payment", "payment_id", payment)
	if err != nil {
		return err
	}
	if snapshot == nil {
		return domain.ErrSubscriptionLifecycleNotPorted
	}
	snapshot.changes["invoice"] = [2]string{invoice, "None"}
	return t.deletePayments(ctx, []*courseSubscriptionSnapshot{snapshot}, true, audit)
}

func (t *subscriptionLifecycleTransaction) AppendHiddenPaymentUpdate(ctx context.Context, payment, association string,
	audit domain.CourseSubscriptionAudit) error {
	changes, err := createdModelChanges(ctx, t.tx, "application_payment", "payment_id", payment)
	if err != nil {
		return err
	}
	live, err := t.liveAssociation(ctx, map[string][2]string{"sport_association": {association, "None"}})
	if err != nil {
		return err
	}
	return appendModelAudit(ctx, t.tx, courseSubscriptionEntry("payment", "Payment", payment, "", 1, changes, live,
		"changes_json.sport_association", audit))
}

// DeleteSubscription is subscription.delete(): Django's Collector from the
// subscription (collaborator_delete_graph.json) with every cascade it
// reaches (course subscriptions and installments with their pre_delete
// receivers, carnet subscriptions, medical appointments, memberships, sold
// products, tokens, files, transfers, camp subscriptions and the
// many-to-many rows), audited per model with the instances in reversed key
// order.
func (t *subscriptionLifecycleTransaction) DeleteSubscription(ctx context.Context, id string, audit domain.CourseSubscriptionAudit) error {
	return t.collectorDelete(ctx, "application.Subscription", id, audit)
}
