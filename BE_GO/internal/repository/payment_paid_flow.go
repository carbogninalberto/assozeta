package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

// PaymentPaidFlows opens the transaction of payment_approve's and
// mark_payment_as_paid's paid flow (invoice-generate's transaction plus the
// course subscription flags and validate_broadcast).
type PaymentPaidFlows struct{ states *PaymentStates }

func NewPaymentPaidFlows(states *PaymentStates) *PaymentPaidFlows {
	return &PaymentPaidFlows{states: states}
}

var _ domain.PaymentPaidFlowStore = (*PaymentPaidFlows)(nil)

type paymentPaidFlowTransaction struct{ *invoiceGenerateTransaction }

func (r *PaymentPaidFlows) BeginPaymentPaidFlow(ctx context.Context) (domain.PaymentPaidFlowTransaction, error) {
	tx, err := r.states.BeginPaymentState(ctx)
	if err != nil {
		return nil, err
	}
	return &paymentPaidFlowTransaction{invoiceGenerateTransaction: &invoiceGenerateTransaction{paymentStateTransaction: tx.(*paymentStateTransaction)}}, nil
}

// BeginPaymentState lets payment_approve run its whole request in the paid-flow transaction.
func (r *PaymentPaidFlows) BeginPaymentState(ctx context.Context) (domain.PaymentStateTransaction, error) {
	return r.BeginPaymentPaidFlow(ctx)
}

func (t *paymentPaidFlowTransaction) LockStripePayment(ctx context.Context, id string, group *string) (*domain.StripePayment, error) {
	return (&stripeRoutesTransaction{paymentStateTransaction: t.paymentStateTransaction}).LockStripePayment(ctx, id, group)
}

func (t *paymentPaidFlowTransaction) PaidFlowOwner(ctx context.Context, association string) (string, domain.InvoiceGenerateOwner, error) {
	var row struct {
		ID string `db:"user_id"`
		domain.InvoiceGenerateOwner
	}
	// payment.sport_association.user (base managers).
	err := t.tx.GetContext(ctx, &row, `SELECT u.user_id, u.balance_sheet_start_day, u.balance_sheet_start_month, u.starting_number_invoices,
 u.payment_date_equal_invoice_date FROM application_sportassociation a JOIN bakney_user u ON u.user_id=a.user_id WHERE a.sport_association_id=$1`,
		association)
	return row.ID, row.InvoiceGenerateOwner, err
}

func (t *paymentPaidFlowTransaction) LastInvoiceNumberByPaymentDate(ctx context.Context, association string, group *string, before bool,
	from time.Time) (*int64, error) {
	// ORDER BY number DESC puts NULL numbers first, like Django's order_by('-number').
	comparison := `p.payment_date >= $3`
	if before {
		comparison = `p.payment_date < $3`
	}
	var number sql.NullInt64
	err := t.tx.GetContext(ctx, &number, `SELECT v.number FROM application_invoice v JOIN application_payment p ON p.invoice_id=v.invoice_id
 WHERE v.sport_association_id=$1 AND NOT v.archived AND NOT v.cancelled AND NOT v.deleted AND ($2::uuid IS NULL OR v.group_id=$2::uuid)
 AND `+comparison+` ORDER BY v.number DESC LIMIT 1`, association, group, from)
	if errors.Is(err, sql.ErrNoRows) || (err == nil && !number.Valid) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &number.Int64, nil
}

func (t *paymentPaidFlowTransaction) MaxInvoiceNumberCreated(ctx context.Context, association string, group *string, from, to time.Time) (*int64, error) {
	var number sql.NullInt64
	if err := t.tx.GetContext(ctx, &number, `SELECT MAX(v.number) FROM application_invoice v
 WHERE v.sport_association_id=$1 AND NOT v.archived AND NOT v.deleted AND ($2::uuid IS NULL OR v.group_id=$2::uuid)
 AND v.creation_date >= $3 AND v.creation_date < $4`, association, group, from, to); err != nil {
		return nil, err
	}
	if !number.Valid {
		return nil, nil
	}
	return &number.Int64, nil
}

func (t *paymentPaidFlowTransaction) SavePaidFlowPayment(ctx context.Context, save domain.PaymentPaidFlowSave) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_payment SET paid=true, payment_date=$2, invoice_id=$3, payment_category_id=$4,
 description=$5 WHERE payment_id=$1`, save.ID, save.PaymentDate, save.Invoice, save.CategoryID, save.Description)
	return err
}

func (t *paymentPaidFlowTransaction) MarkStripePaymentPaid(ctx context.Context, id string, at time.Time) (string, error) {
	var previous string
	err := t.tx.GetContext(ctx, &previous, `WITH old AS (SELECT type FROM application_payment WHERE payment_id=$1)
 UPDATE application_payment SET paid=true, payment_date=$2, type='stripe' WHERE payment_id=$1 RETURNING (SELECT type FROM old)`, id, at)
	return previous, err
}

const paymentPaidFlowCourseSelect = `SELECT ` + courseSubscriptionColumns + ` FROM application_coursesubscription cs
 JOIN application_course c ON c.course_id=cs.course_id`

func (t *paymentPaidFlowTransaction) firstCourse(ctx context.Context, statement string, args ...any) (*domain.CourseSubscriptionRow, error) {
	rows := []domain.CourseSubscriptionRow{}
	if err := t.tx.SelectContext(ctx, &rows, statement, args...); err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return nil, nil
	}
	return &rows[0], nil
}

func (t *paymentPaidFlowTransaction) PaidFlowCourseSubscription(ctx context.Context, payment string, scope *string) (*domain.CourseSubscriptionRow, error) {
	return t.firstCourse(ctx, paymentPaidFlowCourseSelect+` WHERE cs.payment_id=$1 AND NOT cs.deleted
 AND ($2::uuid IS NULL OR (c.sport_association_id=$2::uuid AND EXISTS (SELECT 1 FROM application_subscription s
  WHERE s.subscription_id=cs.subscription_id AND s.sport_association_id=$2::uuid)))
 ORDER BY cs.course_subscription_id LIMIT 1`, payment, scope)
}

func (t *paymentPaidFlowTransaction) PaidFlowInstallment(ctx context.Context, payment string, scope, group *string) (*domain.PaymentPaidFlowInstallment, error) {
	var found []struct {
		ID     string `db:"course_subscription_installment_id"`
		Paid   bool   `db:"paid"`
		Parent string `db:"course_subscription_id"`
	}
	if err := t.tx.SelectContext(ctx, &found, `SELECT n.course_subscription_installment_id, n.paid, n.course_subscription_id
 FROM application_coursesubscriptioninstallment n WHERE n.payment_id=$1 AND NOT n.deleted AND ($2::uuid IS NULL OR n.group_id=$2::uuid)
 AND ($3::uuid IS NULL OR EXISTS (SELECT 1 FROM application_coursesubscription cs JOIN application_course c ON c.course_id=cs.course_id
  JOIN application_subscription s ON s.subscription_id=cs.subscription_id WHERE cs.course_subscription_id=n.course_subscription_id
  AND s.sport_association_id=$3::uuid AND c.sport_association_id=$3::uuid))
 ORDER BY n.course_subscription_installment_id LIMIT 1`, payment, group, scope); err != nil {
		return nil, err
	}
	if len(found) == 0 {
		return nil, nil
	}
	parent, err := t.firstCourse(ctx, paymentPaidFlowCourseSelect+` WHERE cs.course_subscription_id=$1`, found[0].Parent)
	if err != nil {
		return nil, err
	}
	if parent == nil {
		return nil, domain.ErrPaymentStateServer
	}
	return &domain.PaymentPaidFlowInstallment{ID: found[0].ID, Paid: found[0].Paid, Parent: *parent}, nil
}

func (t *paymentPaidFlowTransaction) PaidFlowCarnet(ctx context.Context, payment string, scope *string) (*domain.PaymentPaidFlowCarnet, error) {
	rows := []domain.PaymentPaidFlowCarnet{}
	if err := t.tx.SelectContext(ctx, &rows, `SELECT h.carnet_subscription_id, k.carnet_id IS NOT NULL AS carnet_found, k.title
 FROM application_carnetsubscription h LEFT JOIN application_carnet k ON k.carnet_id=h.carnet_id_id
 WHERE h.payment_id=$1 AND ($2::uuid IS NULL OR (k.sport_association_id=$2::uuid AND EXISTS (SELECT 1 FROM application_subscription s
  WHERE s.subscription_id=h.subscription_id AND s.sport_association_id=$2::uuid)))
 ORDER BY h.carnet_subscription_id LIMIT 1`, payment, scope); err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return nil, nil
	}
	return &rows[0], nil
}

func (t *paymentPaidFlowTransaction) PaidFlowCarnetCourses(ctx context.Context, carnet string) ([]domain.CourseSubscriptionRow, error) {
	rows := []domain.CourseSubscriptionRow{}
	err := t.tx.SelectContext(ctx, &rows, paymentPaidFlowCourseSelect+`
 JOIN application_carnetsubscription_course_subscription l ON l.coursesubscription_id=cs.course_subscription_id
 WHERE l.carnetsubscription_id=$1 AND NOT cs.deleted ORDER BY l.id`, carnet)
	return rows, err
}

// MarkCourseSubscriptionPaid is Model.save() with paid set; the entry is
// indexed through course.sport_association when the live row resolves.
func (t *paymentPaidFlowTransaction) MarkCourseSubscriptionPaid(ctx context.Context, row domain.CourseSubscriptionRow,
	audit domain.CourseSubscriptionAudit) error {
	save := domain.CourseSubscriptionSave{Row: row}
	save.Row.Paid = true
	save.Representation = dto.CourseSubscriptionRepr(save.Row, dto.CourseSubscriptionMemory{})
	if !row.Paid {
		save.Changes = map[string][2]string{"paid": {"False", "True"}}
	}
	if !row.Deleted {
		save.AssociationID, save.ResolutionPath = row.CourseAssociationID, "course.sport_association"
	}
	return t.courses.SaveCourseSubscription(ctx, save, audit)
}

// MarkInstallmentPaid is installment.paid = True; save(): the entry resolves
// through course_subscription.course.sport_association.
func (t *paymentPaidFlowTransaction) MarkInstallmentPaid(ctx context.Context, installment domain.PaymentPaidFlowInstallment,
	audit domain.CourseSubscriptionAudit) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_coursesubscriptioninstallment SET paid=true
 WHERE course_subscription_installment_id=$1`, installment.ID); err != nil {
		return err
	}
	if installment.Paid {
		return nil
	}
	return appendModelAudit(ctx, t.tx, courseSubscriptionEntry("coursesubscriptioninstallment", "CourseSubscriptionInstallment", installment.ID, "", 1,
		map[string][2]string{"paid": {"False", "True"}}, installment.Parent.CourseAssociationID, "course_subscription.course.sport_association", audit))
}

// PaidFlowPeople is payment.associate and payment.user (base managers).
func (t *paymentPaidFlowTransaction) PaidFlowPeople(ctx context.Context, payment string) (domain.PaymentPaidFlowPeople, error) {
	var row domain.PaymentPaidFlowPeople
	err := t.tx.GetContext(ctx, &row, `SELECT a.associate_id IS NOT NULL AS associate_found, a.first_name AS associate_first_name,
 a.last_name AS associate_last_name, u.user_id IS NOT NULL AS user_found, u.user_id::text AS user_id, u.first_name AS user_first_name,
 u.last_name AS user_last_name
 FROM application_payment p LEFT JOIN application_associate a ON a.associate_id=p.associate_id LEFT JOIN bakney_user u ON u.user_id=p.user_id
 WHERE p.payment_id=$1`, payment)
	return row, err
}

// ValidateBroadcast is NotificationManager.validate_broadcast in this transaction.
func (t *paymentPaidFlowTransaction) ValidateBroadcast(ctx context.Context, user, name string, audit domain.LoginAuditContext,
	at time.Time) (bool, error) {
	var broadcast string
	err := t.tx.GetContext(ctx, &broadcast, `SELECT broadcast_id FROM notifications_notificationsbroadcast WHERE broadcast_name=$1
 ORDER BY broadcast_id DESC LIMIT 1`, name)
	if errors.Is(err, sql.ErrNoRows) {
		broadcast = uuid.NewString()
		if _, err = t.tx.ExecContext(ctx, `INSERT INTO notifications_notificationsbroadcast (broadcast_id, broadcast_name) VALUES ($1,$2)`,
			broadcast, name); err != nil {
			return false, err
		}
		if err = notificationAudit(ctx, t.tx, "notificationsbroadcast", "NotificationsBroadcast", "notifications_notificationsbroadcast",
			"broadcast_id", broadcast, audit, at); err != nil {
			return false, err
		}
	} else if err != nil {
		return false, err
	}
	var count int
	if err = t.tx.GetContext(ctx, &count, `SELECT COUNT(*) FROM notifications_usernotificationsbroadcast WHERE broadcast_id=$1 AND user_id=$2`,
		broadcast, user); err != nil {
		return false, err
	}
	if count != 0 {
		return false, nil
	}
	id := uuid.NewString()
	if _, err = t.tx.ExecContext(ctx, `INSERT INTO notifications_usernotificationsbroadcast (user_broadcast_id, user_id, broadcast_id)
 VALUES ($1,$2,$3)`, id, user, broadcast); err != nil {
		return false, err
	}
	return true, notificationAudit(ctx, t.tx, "usernotificationsbroadcast", "UserNotificationsBroadcast", "notifications_usernotificationsbroadcast",
		"user_broadcast_id", id, audit, at)
}
