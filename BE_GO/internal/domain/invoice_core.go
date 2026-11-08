package domain

import (
	"context"
	"errors"
	"time"
)

var (
	// ErrInvoiceCoreServer is an exception Django raises inside the view (a 500).
	ErrInvoiceCoreServer = errors.New("invoice core server error")
	// ErrInvoiceCoreNotPorted refuses a path Go does not implement (a 500 before any write).
	ErrInvoiceCoreNotPorted = errors.New("invoice core path not ported")
	// ErrInvoiceCoreMissing is invoice_list's 404 {'error': 'Invoice not found'}.
	ErrInvoiceCoreMissing = errors.New("Invoice not found")
)

// InvoiceCoreYear is invoice_list's query[current_year] filter: a second
// payment join whose payment_date is within (or outside) the balance year.
type InvoiceCoreYear struct {
	Within   bool
	From, To time.Time
}

// InvoiceCoreFilter is invoice_list's queryset.
type InvoiceCoreFilter struct {
	AssociationID string
	Scope, Group  *string
	Archived      bool
	Year          *InvoiceCoreYear
	// AmountText (an icontains pattern of number) and AmountExact (a fee
	// equal to the Decimal) are the float search.
	AmountText, AmountExact *string
	// Smart is smart_search (it makes the queryset distinct).
	Smart *PaymentReadsSmartSearch
	Order []PaymentReadsOrder
}

// InvoiceCorePayment is one invoice's first prefetched payment.
type InvoiceCorePayment struct {
	InvoiceID string `db:"invoice_id"`
	PaymentID string `db:"payment_id"`
}

// InvoiceCoreListPayment is what PaymentInvoiceSerializer renders.
type InvoiceCoreListPayment struct {
	Payment   InvoicePartyPayment
	Associate *PaymentReadsAssociate
	// Relations, Second and Third are the associate's tutor relations and the tutors' own.
	Relations, Second, Third []PersonaRelation
}

// InvoiceCoreArchivedPayment is what PaymentSerializer renders.
type InvoiceCoreArchivedPayment struct {
	CourseSubscriptionPayment
	Instructor *InstructorRow
	Invoice    *PaymentReadsInvoice
}

// InvoiceCorePaymentSave is the payment invoice_delete unlinks and saves.
type InvoiceCorePaymentSave struct {
	PaymentSaveRow
	Paid bool `db:"paid"`
	// Association is the stored sport_association (the audit index resolver).
	Association *string `db:"association"`
}

type InvoiceCoreStore interface {
	// UserAssociation is request.user.sportassociation (any state); nil when absent.
	UserAssociation(ctx context.Context, user string) (*string, error)
	// ScopedAssociation is the scoped live SportAssociation .get(user=...); ErrAssociationNotFound when absent.
	ScopedAssociation(ctx context.Context, user string, scope *string) (string, error)
	BalanceStart(ctx context.Context, user string) (day, month int64, err error)
	// Invoice is the live, group-aware (scoped) invoice by id; nil when absent.
	Invoice(ctx context.Context, id string, group, scope *string) (*PaymentReadsInvoice, error)
	CountInvoices(ctx context.Context, filter InvoiceCoreFilter) (int64, error)
	// InvoiceRows are the page's invoice ids, one per queryset row.
	InvoiceRows(ctx context.Context, filter InvoiceCoreFilter, limit, offset int64) ([]string, error)
	Invoices(ctx context.Context, ids []string) (map[string]PaymentReadsInvoice, error)
	// ArchivedInvoices are the association's live, group-aware archived invoices by -creation_date.
	ArchivedInvoices(ctx context.Context, association string, group, scope *string) ([]PaymentReadsInvoice, error)
	// FirstPayments are the first live, group-aware (scoped) payment per invoice:
	// in the database's order (byKey=false, the prefetch) or by key (.first()).
	FirstPayments(ctx context.Context, invoices []string, group, scope *string, byKey bool) (map[string]string, error)
	ListPayments(ctx context.Context, ids []string, group *string) (map[string]InvoiceCoreListPayment, error)
	ArchivedPayments(ctx context.Context, ids []string, group *string) (map[string]InvoiceCoreArchivedPayment, error)
	BeginInvoiceCore(ctx context.Context) (InvoiceCoreTransaction, error)
}

// InvoiceCoreDeleteContext is the audit context of one deletion.
type InvoiceCoreDeleteContext struct {
	Audit LoginAuditContext
	At    time.Time
}

type InvoiceCoreTransaction interface {
	PaymentArchiveTransaction
	ScopedAssociation(ctx context.Context, user string, scope *string) (string, error)
	// MatchingInvoices locks the association's live, group-aware (scoped) invoices among ids.
	MatchingInvoices(ctx context.Context, association string, ids []string, group, scope *string) ([]string, error)
	ArchiveInvoices(ctx context.Context, ids []string) error
	// PaymentToUnlink locks the first live, group-aware (scoped) payment of the invoice by key.
	PaymentToUnlink(ctx context.Context, invoice string, group, scope *string) (*InvoiceCorePaymentSave, error)
	UnlinkPayment(ctx context.Context, row InvoiceCorePaymentSave) error
	// AppendPaymentSave audits the payment save (indexed to its association, else the actor's).
	AppendPaymentSave(ctx context.Context, row InvoiceCorePaymentSave, changes map[string][2]string, audit InvoiceCoreDeleteContext) error
	// DeleteInvoices is QuerySet.delete() over the invoices with its audit entries.
	DeleteInvoices(ctx context.Context, ids []string, audit InvoiceCoreDeleteContext) error
}
