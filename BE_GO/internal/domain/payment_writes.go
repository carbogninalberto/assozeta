package domain

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

var (
	// ErrPaymentWritesServer is an unhandled exception in the Django view (the 500 page).
	ErrPaymentWritesServer = errors.New("payment writes server error")
	// ErrPaymentWritesNotPorted refuses, before any write, an input whose
	// Django behaviour is not ported (invoice creation, PDF tasks, document
	// storage, nested user writes, primary key changes, form bodies).
	ErrPaymentWritesNotPorted = refusal.New("payment writes behaviour not ported")
	// ErrPaymentWritesAthlete is the views' Response(status=403) for athletes.
	ErrPaymentWritesAthlete = errors.New("payment writes athlete")
	// ErrPaymentWritesForeignReference is the tenant-scope PermissionDenied
	// raised while a related field validates.
	ErrPaymentWritesForeignReference = errors.New("payment writes foreign reference")
	// ErrPaymentWritesAttachment is _handle_documents' ValidationError
	// {'exception': 'document key not present'} (after the save).
	ErrPaymentWritesAttachment = errors.New("document key not present")
)

// PaymentWritesMessage is a view's own 400 {'msg': ...} response.
type PaymentWritesMessage struct{ Message string }

func (e *PaymentWritesMessage) Error() string { return e.Message }

// PaymentWriteRow is every stored Payment column; JSON columns are their
// JSON text (nil is SQL NULL).
type PaymentWriteRow struct {
	ID                string     `db:"payment_id"`
	Type              string     `db:"type"`
	Subject           int        `db:"subject"`
	Amount            string     `db:"amount"`
	CreationDate      time.Time  `db:"creation_date"`
	PaymentDate       *time.Time `db:"payment_date"`
	Paid              bool       `db:"paid"`
	Expense           bool       `db:"expense"`
	Description       *string    `db:"description"`
	CategoryID        *string    `db:"payment_category_id"`
	MetaCategories    *string    `db:"meta_payment_categories"`
	InvoiceID         *string    `db:"invoice_id"`
	UserID            *string    `db:"user_id"`
	AssociateID       *string    `db:"associate_id"`
	SupplierID        *string    `db:"supplier_id"`
	InstructorID      *string    `db:"instructor_id"`
	AssociationID     *string    `db:"sport_association_id"`
	PaymentIntentID   *string    `db:"payment_intent_id"`
	Archived          bool       `db:"archived"`
	InvoiceFiscalCode *string    `db:"invoice_fiscal_code"`
	Meta              *string    `db:"meta"`
	Notes             *string    `db:"notes"`
	AccountID         *string    `db:"custom_accounts_id"`
	SignatureID       *string    `db:"signature_id"`
	Deleted           bool       `db:"deleted"`
	GroupID           *string    `db:"group_id"`
	Course            *string    `db:"course"`
}

// PaymentWritesAuditEntry is one Payment auditlog entry with its index row
// (Association nil: no index).
type PaymentWritesAuditEntry struct {
	PaymentID   string
	Action      int
	Changes     map[string][2]string
	Context     LoginAuditContext
	Association *string
	Path        string
	At          time.Time
}

// PaymentBulkRow is one payment payment_bulk_add builds; Amount is the
// str(Decimal) the view passes to the numeric column.
type PaymentBulkRow struct {
	ID, AssociateID, UserID, AssociationID, Type string
	Subject                                      int
	Amount                                       string
	Description, Notes, CategoryID, AccountID    *string
	Paid, Expense                                bool
	CreationDate                                 time.Time
	PaymentDate                                  *time.Time
	Meta                                         *string
}

type PaymentWritesStore interface {
	BeginPaymentWrites(context.Context) (PaymentWritesTransaction, error)
}

type PaymentWritesTransaction interface {
	PaymentStateTransaction
	// TenantAssociation is tenant_scope.current_association: the user's first live association.
	TenantAssociation(ctx context.Context, user string) (*string, error)
	AutoPaid(ctx context.Context, user string) (bool, error)
	// LockPayment is scoped_queryset(request, Payment.objects).filter(payment_id=id).first() (locked).
	LockPayment(ctx context.Context, id string, group, scope *string) (*PaymentWriteRow, error)
	// RelatedOwner is a related row's existence and association (table: payment category, custom account).
	RelatedOwner(ctx context.Context, table, id string) (bool, *string, error)
	// AssociationExists is SportAssociation._default_manager (deleted included).
	AssociationExists(ctx context.Context, id string) (bool, error)
	// LiveAssociation is SportAssociation.objects.filter(pk=id).first() (the audit index resolver).
	LiveAssociation(ctx context.Context, id string) (bool, error)
	CategoryName(ctx context.Context, id string) (*string, error)
	DefaultBankAccount(ctx context.Context, association string) (*string, error)
	GroupExists(ctx context.Context, id string) (bool, error)
	// InstructorOwner is Instructor.objects.get(...): whether it exists and its user_id.
	InstructorOwner(ctx context.Context, id string) (bool, *string, error)
	// SupplierAssociation is SupplierAndCustomers.objects (live, group-aware): whether it exists and its
	// sport_association_id.
	SupplierAssociation(ctx context.Context, id string, group *string) (bool, *string, error)
	// SubscriptionAssociate is scoped_queryset(request, Subscription.objects).get(...): whether it exists,
	// its associate_id and its sport_association_id.
	SubscriptionAssociate(ctx context.Context, id string, group, scope *string) (bool, *string, *string, error)
	// AssociateAssociation is scoped_queryset(request, Associate.objects) (live, group-aware): whether it
	// exists and its sport_association_id.
	AssociateAssociation(ctx context.Context, id string, group, scope *string) (bool, *string, error)
	InsertPaymentRow(ctx context.Context, row PaymentWriteRow) error
	// AttachPaymentDocument is Document.objects.create(filename=...) with its audited create
	// and obj.attachments.add(document) (no audit entry for the through row).
	AttachPaymentDocument(ctx context.Context, payment string, document NewDocument, actorAssociation *string, audit LoginAuditContext,
		at time.Time) error
	SavePaymentRow(ctx context.Context, row PaymentWriteRow) error
	// PaymentAuditValues renders the stored row as auditlog's get_field_value strings by field name.
	PaymentAuditValues(ctx context.Context, id string) (map[string]string, error)
	AppendPaymentWritesAudit(ctx context.Context, entry PaymentWritesAuditEntry) error
	// BulkAssociates is the matching live group-aware associates of the association.
	BulkAssociates(ctx context.Context, ids []string, association string, group, scope *string) ([]string, error)
	// AssociationCategoryExists is PaymentCategory.get_all_categories(association).filter(pk=id).exists().
	AssociationCategoryExists(ctx context.Context, id, association string) (bool, error)
	// AssociationAccount is CustomAccounts.objects.filter(pk=id, sport_association=association).first().
	AssociationAccount(ctx context.Context, id, association string) (*string, error)
	InsertBulkPayments(ctx context.Context, rows []PaymentBulkRow) error
	// InvoiceGenerate is the invoice generation reads and writes on this transaction (payment_add's paid branch).
	InvoiceGenerate() InvoiceGenerateTransaction
}
