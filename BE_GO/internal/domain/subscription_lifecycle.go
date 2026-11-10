package domain

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

var (
	// ErrSubscriptionLifecycleNotFound is the view's own "Subscription not found" response.
	ErrSubscriptionLifecycleNotFound = errors.New("subscription not found")
	// ErrSubscriptionLifecycleNotPorted is a path the Go port refuses (500) before any write.
	ErrSubscriptionLifecycleNotPorted = refusal.New("subscription lifecycle path not ported")
)

// SubscriptionLifecycleRow is a locked subscription with the columns the
// reject, archive and delete views read, and its association owner's date
// settings (Subscription.save()).
type SubscriptionLifecycleRow struct {
	ID            string     `db:"subscription_id"`
	AssociationID string     `db:"sport_association_id"`
	AssociateID   *string    `db:"associate_id"`
	Status        int        `db:"status_flag"`
	Acceptance    *time.Time `db:"acceptance_date"`
	Start         *time.Time `db:"start_date"`
	End           *time.Time `db:"end_date"`
	Created       time.Time  `db:"creation_date"`
	Archived      bool       `db:"archived"`
	PaymentID     *string    `db:"payment_id"`
	SignatureKey  *string    `db:"signature_storage_key"`
	StartDay      int        `db:"subscription_start_day"`
	StartMonth    int        `db:"subscription_start_month"`
	CustomEnd     bool       `db:"custom_end_date"`
	EndDay        int        `db:"subscription_end_day"`
	EndMonth      int        `db:"subscription_end_month"`
	Duration      int        `db:"subscription_duration"`
}

// SubscriptionLifecycleSave is the subscription's Model.save(): the columns
// the views change and the audited differences.
type SubscriptionLifecycleSave struct {
	ID, AssociationID string
	Status            int
	Acceptance        *string
	Start, End        *string
	Archived          bool
	PaymentID         *string
	Changes           map[string][2]string
}

// SubscriptionLifecyclePayment is a payment read through the base manager
// (forward foreign keys), with the columns Payment.save() reads.
type SubscriptionLifecyclePayment struct {
	PaymentSaveRow
	Paid      bool    `db:"paid"`
	InvoiceID *string `db:"invoice_id"`
	Deleted   bool    `db:"deleted"`
	GroupID   *string `db:"group_id"`
}

type SubscriptionLifecycleInstallment struct {
	ID        string  `db:"course_subscription_installment_id"`
	PaymentID *string `db:"payment_id"`
	Archived  bool    `db:"archived"`
}

type SubscriptionLifecycleStore interface {
	BeginSubscriptionLifecycle(context.Context) (SubscriptionLifecycleTransaction, error)
}

type SubscriptionLifecycleTransaction interface {
	// LifecycleAssociation is scoped_queryset(SportAssociation.objects).get(user=owner).
	LifecycleAssociation(ctx context.Context, owner string, scope *string) (string, error)
	ActorAssociation(ctx context.Context, actor string) (*string, error)
	// Subscription is scoped_queryset(Subscription.objects) (group-aware,
	// soft-deleted rows included) filtered by pk, locked; nil when none.
	Subscription(ctx context.Context, id string, scope, group *string) (*SubscriptionLifecycleRow, error)
	SaveSubscription(ctx context.Context, save SubscriptionLifecycleSave, audit CourseSubscriptionAudit) error
	// LiveCourseSubscriptions is CourseSubscription.objects.filter(subscription=...)
	// (optionally scoped), in the table's physical order.
	LiveCourseSubscriptions(ctx context.Context, subscription string, scope *string) ([]CourseSubscriptionRow, error)
	// LiveInstallments is CourseSubscriptionInstallment.objects (live,
	// group-aware) of a course subscription, in physical order.
	LiveInstallments(ctx context.Context, row string, group *string) ([]SubscriptionLifecycleInstallment, error)
	Payment(ctx context.Context, id string) (*SubscriptionLifecyclePayment, error)
	PaymentSaves() PaymentArchiveTransaction
	ArchiveInstallment(ctx context.Context, installment string, association *string, audit CourseSubscriptionAudit) error
	SaveCourseSubscription(ctx context.Context, save CourseSubscriptionSave, audit CourseSubscriptionAudit) error
	// RangePayments is Payment.objects (live, group-aware) of the association
	// and associate created within [from, to], unarchived and unpaid, in physical order.
	RangePayments(ctx context.Context, association string, associate *string, from, to time.Time, group *string) ([]string, error)
	// DeletePayments is payment.delete() / QuerySet.delete() with Payment's pre_delete.
	DeletePayments(ctx context.Context, ids []string, audit CourseSubscriptionAudit) error
	// DeleteLoopInstallment is installment.payment.delete(), then
	// installment.payment = None and installment.delete(); invoice is the
	// payment's invoice_id still held in memory after its invoice was deleted.
	DeleteLoopInstallment(ctx context.Context, installment SubscriptionLifecycleInstallment, invoice *string, audit CourseSubscriptionAudit) error
	// DeleteInvoice is payment.invoice.delete(): the Collector from the invoice
	// (its rows, the many-to-many rows, Payment.invoice set NULL).
	DeleteInvoice(ctx context.Context, invoice string, audit CourseSubscriptionAudit) error
	// DeleteInvoicedPayment is payment.delete() of an instance whose invoice
	// was deleted: auditlog renders the invoice_id still held in memory.
	DeleteInvoicedPayment(ctx context.Context, payment, invoice string, audit CourseSubscriptionAudit) error
	// AppendHiddenPaymentUpdate is auditlog's update entry for a payment
	// Payment.objects does not find: the diff against None of the stored row.
	AppendHiddenPaymentUpdate(ctx context.Context, payment, association string, audit CourseSubscriptionAudit) error
	// AppendUnownedPaymentUpdate is auditlog's update entry for a payment
	// without an association (indexed through the actor).
	AppendUnownedPaymentUpdate(ctx context.Context, payment string, changes map[string][2]string, audit CourseSubscriptionAudit) error
	DeleteInstallmentsOf(ctx context.Context, row string, audit CourseSubscriptionAudit) error
	// DeleteSubscription is subscription.delete(): the collector with its cascades.
	DeleteSubscription(ctx context.Context, id string, audit CourseSubscriptionAudit) error
	Commit() error
	Rollback() error
}
