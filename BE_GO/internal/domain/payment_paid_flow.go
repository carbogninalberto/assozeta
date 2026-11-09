package domain

import (
	"context"
	"time"
)

// PaymentPaidFlowInstallment is the first installment paid by a payment and
// its parent course subscription (base manager).
type PaymentPaidFlowInstallment struct {
	ID     string
	Paid   bool
	Parent CourseSubscriptionRow
}

// PaymentPaidFlowCarnet is the first carnet subscription paid by a payment;
// CarnetFound is false for a null carnet.
type PaymentPaidFlowCarnet struct {
	ID          string  `db:"carnet_subscription_id"`
	CarnetFound bool    `db:"carnet_found"`
	Title       *string `db:"title"`
}

// PaymentPaidFlowPeople are the names the notification reads (associate, user).
type PaymentPaidFlowPeople struct {
	AssociateFound bool    `db:"associate_found"`
	AssociateFirst *string `db:"associate_first_name"`
	AssociateLast  *string `db:"associate_last_name"`
	UserFound      bool    `db:"user_found"`
	UserID         *string `db:"user_id"`
	UserFirst      *string `db:"user_first_name"`
	UserLast       *string `db:"user_last_name"`
}

// PaymentPaidFlowSave is payment.save() of the paid flow (paid, payment date,
// the new invoice and Payment.save()'s defaults).
type PaymentPaidFlowSave struct {
	ID          string
	PaymentDate time.Time
	Invoice     *string
	CategoryID  *string
	Description *string
}

type PaymentPaidFlowStore interface {
	BeginPaymentPaidFlow(context.Context) (PaymentPaidFlowTransaction, error)
}

// PaymentPaidFlowTransaction is one transaction of payment_approve's or
// mark_payment_as_paid's paid flow: invoice numbering and creation, the
// payment save, the course subscription paid flags and validate_broadcast.
type PaymentPaidFlowTransaction interface {
	InvoiceGenerateTransaction
	// LockStripePayment is mark_payment_as_paid's select_for_update().get().
	LockStripePayment(ctx context.Context, id string, group *string) (*StripePayment, error)
	// PaidFlowOwner is the association owner's invoice settings and id.
	PaidFlowOwner(ctx context.Context, association string) (string, InvoiceGenerateOwner, error)
	// LastInvoiceNumberByPaymentDate is get_next_invoice_number(use_payment_date=True)'s
	// order_by('-number').first(): nil for no row or a NULL number.
	LastInvoiceNumberByPaymentDate(ctx context.Context, association string, group *string, before bool, from time.Time) (*int64, error)
	// MaxInvoiceNumberCreated is aggregate(Max('number')) of the live,
	// group-aware, unarchived invoices created in [from, to).
	MaxInvoiceNumberCreated(ctx context.Context, association string, group *string, from, to time.Time) (*int64, error)
	SavePaidFlowPayment(ctx context.Context, save PaymentPaidFlowSave) error
	// MarkStripePaymentPaid is mark_payment_as_paid's payment.type = 'stripe'
	// and payment.save(update_fields=['paid', 'payment_date', 'type']): it
	// writes those three columns and returns the previous type.
	MarkStripePaymentPaid(ctx context.Context, id string, at time.Time) (string, error)
	// PaidFlowCourseSubscription is CourseSubscription.objects.filter(payment=...).first()
	// (live; scope adds the impersonation boundaries).
	PaidFlowCourseSubscription(ctx context.Context, payment string, scope *string) (*CourseSubscriptionRow, error)
	// PaidFlowInstallment is CourseSubscriptionInstallment.objects (live, group-aware).filter(payment=...).first().
	PaidFlowInstallment(ctx context.Context, payment string, scope, group *string) (*PaymentPaidFlowInstallment, error)
	// PaidFlowCarnet is CarnetSubscription.objects.filter(payment=...).first().
	PaidFlowCarnet(ctx context.Context, payment string, scope *string) (*PaymentPaidFlowCarnet, error)
	// PaidFlowCarnetCourses is carnet_subscription.course_subscription.all() (live rows).
	PaidFlowCarnetCourses(ctx context.Context, carnet string) ([]CourseSubscriptionRow, error)
	// MarkCourseSubscriptionPaid is course_subscription.paid = True; save() with its update entry when it changed.
	MarkCourseSubscriptionPaid(ctx context.Context, row CourseSubscriptionRow, audit CourseSubscriptionAudit) error
	// MarkInstallmentPaid is installment.paid = True; save() with its update entry when it changed.
	MarkInstallmentPaid(ctx context.Context, installment PaymentPaidFlowInstallment, audit CourseSubscriptionAudit) error
	PaidFlowPeople(ctx context.Context, payment string) (PaymentPaidFlowPeople, error)
	// ValidateBroadcast is NotificationManager.validate_broadcast in this transaction.
	ValidateBroadcast(ctx context.Context, user, name string, audit LoginAuditContext, at time.Time) (bool, error)
}
