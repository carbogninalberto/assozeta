package domain

import (
	"context"
	"errors"
	"time"
)

// ErrInvoiceGenerateSelect is payment_generate_invoice's
// 400 {'error': "Seleziona l'associato."} (no subscription_id).
var ErrInvoiceGenerateSelect = errors.New("invoice generate: subscription required")

// InvoiceGeneratePayment is what the view, the fees and
// generate_invoice_description read from the locked payment (pre-save values).
type InvoiceGeneratePayment struct {
	Amount                string     `db:"amount"`
	CreationDate          time.Time  `db:"creation_date"`
	PaymentDate           *time.Time `db:"payment_date"`
	MetaPaymentCategories *string    `db:"meta_payment_categories"`
	SupplierName          *string    `db:"supplier_name"`
	HasInstructor         bool       `db:"has_instructor"`
	InstructorFirstName   *string    `db:"instructor_first_name"`
	InstructorLastName    *string    `db:"instructor_last_name"`
}

// InvoiceGenerateOwner holds the owner's invoice settings (request.user).
type InvoiceGenerateOwner struct {
	StartDay         int  `db:"balance_sheet_start_day"`
	StartMonth       int  `db:"balance_sheet_start_month"`
	StartingNumber   int  `db:"starting_number_invoices"`
	PaymentDateEqual bool `db:"payment_date_equal_invoice_date"`
}

// InvoiceGenerateNumberQuery is get_next_invoice_number's queryset: the live,
// group-aware, unarchived invoices of the association created before From
// (Before) or joined to a payment created from From on.
type InvoiceGenerateNumberQuery struct {
	Association string
	Group       *string
	Before      bool
	From        time.Time
}

// InvoiceGeneratePeriod is a subscription or membership's dates (get_period).
type InvoiceGeneratePeriod struct {
	Start *time.Time `db:"start_date"`
	End   *time.Time `db:"end_date"`
}

// InvoiceGenerateMembership is the first SubscriptionMembership of a subscription.
type InvoiceGenerateMembership struct {
	InvoiceGeneratePeriod
	Type *string `db:"membership_type"`
}

// InvoiceGenerateTitle is the title generate_invoice_description reads from
// a course or carnet subscription paid by the payment; Missing is a carnet
// subscription without a carnet (AttributeError).
type InvoiceGenerateTitle struct {
	Found   bool
	Missing bool
	Title   *string
}

// InvoiceGenerateCreate is Invoice.objects.create(...) of the view.
type InvoiceGenerateCreate struct {
	ID, Association                    string
	CreationDate                       time.Time
	MembershipFee, ActivityFee         string // numeric text the column rounds
	MetaPaymentCategories, Description *string
	Number                             int64
	Group                              *string
}

// InvoiceGenerateSave is payment.save() after the invoice is attached.
type InvoiceGenerateSave struct {
	ID, Invoice             string
	Associate               *string
	CategoryID, Description *string
}

type InvoiceGenerateStore interface {
	BeginInvoiceGenerate(context.Context) (InvoiceGenerateTransaction, error)
}

type InvoiceGenerateTransaction interface {
	PaymentStateTransaction
	InvoiceGeneratePayment(ctx context.Context, id string) (InvoiceGeneratePayment, error)
	// GenerateSubscription is scoped_queryset(Subscription.objects).filter(subscription_id=...).first():
	// found, and its associate.
	GenerateSubscription(ctx context.Context, id string, scope, group *string) (bool, *string, error)
	// LockInvoiceNumbering is InvoiceService._acquire_invoice_lock (pg_advisory_xact_lock).
	LockInvoiceNumbering(ctx context.Context, association string) error
	InvoiceGenerateOwner(ctx context.Context, owner string) (InvoiceGenerateOwner, error)
	// LastInvoiceNumber is order_by('-number').first(): nil when there is no
	// row or the first row's number is NULL.
	LastInvoiceNumber(ctx context.Context, query InvoiceGenerateNumberQuery) (*int64, error)
	AssociationDenomination(ctx context.Context, association string) (*string, error)
	AssociateNames(ctx context.Context, associate string) (first, last *string, err error)
	SubscriptionByPayment(ctx context.Context, payment string, group *string) (*string, *InvoiceGeneratePeriod, error)
	FirstMembership(ctx context.Context, subscription string) (*InvoiceGenerateMembership, error)
	CourseTitle(ctx context.Context, payment string, group *string) (InvoiceGenerateTitle, error)
	// CreateInvoice inserts the invoice and its create entry; createChanges
	// overrides the entry's in-memory values (the float fees).
	CreateInvoice(ctx context.Context, create InvoiceGenerateCreate, overrides map[string]string, audit LoginAuditContext, at time.Time) error
	// SetInvoiceCreationDate is invoice.save() with the payment date and its update entry.
	SetInvoiceCreationDate(ctx context.Context, invoice, association string, date time.Time, changes map[string][2]string,
		audit LoginAuditContext, at time.Time) error
	SaveGeneratedPayment(ctx context.Context, save InvoiceGenerateSave) error
}
