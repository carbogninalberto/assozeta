package domain

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

var (
	// ErrPaymentStateServer is an unhandled exception in the Django view (the 500 page).
	ErrPaymentStateServer = errors.New("payment state server error")
	// ErrPaymentStateNotPorted refuses, before any write, a path whose Django
	// behaviour is not ported (invoice creation, notifications, PDF tasks).
	ErrPaymentStateNotPorted = refusal.New("payment state behaviour not ported")
	// ErrPaymentStateNotFound is payment_delete's 404 {'msg': 'Payment not found.'}.
	ErrPaymentStateNotFound = errors.New("payment not found")
	// ErrPaymentStateNotPaid is payment_cancel's 400 {'error': 'Payment not paid.'}.
	ErrPaymentStateNotPaid = errors.New("payment not paid")
)

// PaymentStateRow is a locked live payment with what the approve, cancel and
// delete views read; AssociationID is empty for a payment without one.
type PaymentStateRow struct {
	PaymentSaveRow
	Paid         bool       `db:"paid"`
	Expense      bool       `db:"expense"`
	PaymentDate  *time.Time `db:"payment_date"`
	InvoiceID    *string    `db:"invoice_id"`
	AssociateID  *string    `db:"associate_id"`
	SupplierID   *string    `db:"supplier_id"`
	InstructorID *string    `db:"instructor_id"`
}

// PaymentStateQuery is Payment.objects (live, X-Group-Id aware) by id, with
// the view's association filter and scoped_queryset's session association.
type PaymentStateQuery struct {
	ID          string
	Association *string
	Scope       *string
	Group       *string
}

// PaymentStateRender is what PaymentSerializer reads for one payment.
type PaymentStateRender struct {
	Payment    CourseSubscriptionPayment
	Instructor *InstructorRow
	// Invoice is the payment's invoice (InvoiceSerializer), nil without one.
	Invoice *PaymentReadsInvoice
}

// PaymentStateSupplierInvoice is the first InvoiceSuppliers of a payment.
type PaymentStateSupplierInvoice struct {
	ID            string     `db:"invoice_supplier_id"`
	AssociationID string     `db:"sport_association_id"`
	Paid          bool       `db:"paid"`
	PaymentDate   *time.Time `db:"payment_date"`
}

// PaymentStateSave is Payment.save() of the approve and cancel views.
type PaymentStateSave struct {
	ID          string
	Paid        bool
	PaymentDate *time.Time
	CategoryID  *string
	Description *string
}

type PaymentStateStore interface {
	BeginPaymentState(context.Context) (PaymentStateTransaction, error)
}

type PaymentStateTransaction interface {
	PaymentArchiveTransaction
	// ScopedAssociation is scoped_queryset(SportAssociation.objects).get(user=owner).
	ScopedAssociation(ctx context.Context, owner string, scope *string) (string, error)
	// ActorAssociation is the audit index fallback (the actor's own live association).
	ActorAssociation(ctx context.Context, actor string) (*string, error)
	// PaymentState locks the matching payment; nil when there is none.
	PaymentState(ctx context.Context, query PaymentStateQuery) (*PaymentStateRow, error)
	RenderPayment(ctx context.Context, id string, group *string) (PaymentStateRender, error)
	SavePaymentState(ctx context.Context, save PaymentStateSave) error
	// SupplierInvoice is InvoiceSuppliers.objects.filter(payment=...).first() (locked).
	SupplierInvoice(ctx context.Context, payment string) (*PaymentStateSupplierInvoice, error)
	SaveSupplierInvoice(ctx context.Context, id string, paid bool, paymentDate *time.Time) error
	AppendSupplierInvoiceAudit(ctx context.Context, invoice PaymentStateSupplierInvoice, changes map[string][2]string, audit LoginAuditContext,
		at time.Time) error
	// DeletePayment is payment.delete() with Payment's pre_delete signal connected.
	DeletePayment(ctx context.Context, id string, audit CourseSubscriptionAudit) error
	// PaymentStateInvoice locks the payment's invoice (nil when the row is gone).
	PaymentStateInvoice(ctx context.Context, id string) (*PaymentStateInvoice, error)
	// CancelPaymentStateInvoice is invoice.cancelled = True, invoice.meta = meta,
	// invoice.save() with its auditlog update entry (changed fields only).
	CancelPaymentStateInvoice(ctx context.Context, invoice PaymentStateInvoice, meta string, metaAudit string, audit LoginAuditContext,
		at time.Time) error
	// MarkPayments is bulk_update(..., [column]) setting column true on the
	// matching live rows of the association.
	MarkPayments(ctx context.Context, column string, ids []string, association string, scope, group *string) error
}

// PaymentStateInvoice is the stored invoice payment cancel and delete save.
type PaymentStateInvoice struct {
	ID            string  `db:"invoice_id"`
	AssociationID *string `db:"sport_association_id"`
	Cancelled     bool    `db:"cancelled"`
	// Meta is the stored JSON text; nil for SQL NULL.
	Meta *string `db:"meta"`
}
