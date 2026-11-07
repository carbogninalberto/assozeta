package domain

import (
	"context"
	"errors"
	"time"
)

var (
	// ErrPaymentReadsServer is an exception Django does not handle (the 500 page).
	ErrPaymentReadsServer = errors.New("payment reads unhandled error")
	// ErrPaymentReadsNotPorted refuses inputs whose Django behaviour is not
	// ported (the 500 page, never a guessed body).
	ErrPaymentReadsNotPorted = errors.New("payment reads input not ported")
	// ErrPaymentReadsAthlete is subscription_payments' Response(status=401) for athletes.
	ErrPaymentReadsAthlete = errors.New("payment reads athlete")
)

// PaymentReadsOrder is one order_by() column of Payment.
type PaymentReadsOrder struct {
	Column     string
	Descending bool
}

// PaymentReadsSmartSearch is sqlite_search over Payment (patterns already escaped).
type PaymentReadsSmartSearch struct {
	Words        []string
	Dates        [][]string
	Permutations []string
}

// PaymentReadsFilter is the payment queryset the three views build.
type PaymentReadsFilter struct {
	AssociationID string
	// Scope is scoped_queryset's session association (nil outside one).
	Scope *string
	// Group is the X-Group-Id of the group-aware managers.
	Group *string
	// Archived is the archived=... filter (nil: no filter).
	Archived *bool
	// Empty is a lookup Django compiles to EmptyResultSet (no rows).
	Empty       bool
	AssociateID *string
	// From/To is the payment_date range (payment_date set) or the creation_date range (payment_date null).
	From, To    *time.Time
	Subject     *int64
	Type        *string
	Paid        *bool
	Expense     *bool
	NoCategory  bool
	Categories  []string
	NoAccount   bool
	Account     *string
	AmountText  *string
	AmountExact *string
	Smart       *PaymentReadsSmartSearch
	// Member is subscription_payments' associate scope (with the optional
	// iexact tax code); MemberText its icontains search, which repeats a
	// payment once per joined course subscription and installment row.
	Member        *string
	MemberTaxCode *string
	MemberText    *string
	Order         []PaymentReadsOrder
	Random        bool
}

// PaymentReadsCompressed is one values() row of the compressed mode.
type PaymentReadsCompressed struct {
	ID                  string     `db:"payment_id"`
	Amount              string     `db:"amount"`
	PaymentDate         *time.Time `db:"payment_date"`
	Paid                bool       `db:"paid"`
	AssociateID         *string    `db:"associate_id"`
	SupplierID          *string    `db:"supplier_id"`
	InstructorID        *string    `db:"instructor_id"`
	AssociateFirstName  *string    `db:"associate_first_name"`
	AssociateLastName   *string    `db:"associate_last_name"`
	SupplierName        *string    `db:"supplier_name"`
	InstructorFirstName *string    `db:"instructor_first_name"`
	InstructorLastName  *string    `db:"instructor_last_name"`
	AccountName         *string    `db:"account_name"`
}

// PaymentReadsAssociate is AssociateOptimizedSerializer's own columns.
type PaymentReadsAssociate struct {
	ID        string  `db:"associate_id"`
	FirstName *string `db:"first_name"`
	LastName  *string `db:"last_name"`
	IsMinor   bool    `db:"is_minor"`
}

// PaymentReadsInvoice is one Invoice with what InvoiceSerializer renders.
type PaymentReadsInvoice struct {
	ID                    string    `db:"invoice_id"`
	DocumentID            *string   `db:"document_pdf_id"`
	DocumentToken         *string   `db:"document_token"`
	AssociationID         string    `db:"sport_association_id"`
	CreationDate          time.Time `db:"creation_date"`
	MembershipFee         string    `db:"membership_fee"`
	ActivityFee           string    `db:"activity_fee"`
	MetaPaymentCategories *string   `db:"meta_payment_categories"`
	Description           *string   `db:"description"`
	Number                *int64    `db:"number"`
	Archived              bool      `db:"archived"`
	Cancelled             bool      `db:"cancelled"`
	SelectedTutorID       *string   `db:"selected_tutor_id"`
	Meta                  *string   `db:"meta"`
	Imported              *bool     `db:"imported_from_associami"`
	GroupID               *string   `db:"group_id"`
	Deleted               bool      `db:"deleted"`
	Attached              string    `db:"attached_documents"`
}

// PaymentReadsCourse is get_course's {'title', 'course_id'}.
type PaymentReadsCourse struct {
	ID    string  `db:"course_id"`
	Title *string `db:"title"`
}

// PaymentReadsRender is what PaymentOptimizedSerializer reads for one payment.
type PaymentReadsRender struct {
	Payment   InvoicePartyPayment
	Associate *PaymentReadsAssociate
	Relations []PersonaRelation
	// Second and Third are the M2M managers' tutors one and two levels down.
	Second     []PersonaRelation
	Third      []PersonaRelation
	Instructor *InstructorRow
	Invoice    *PaymentReadsInvoice
	// Course is get_course's related course (nil: the payment's course JSON, if truthy).
	Course      *PaymentReadsCourse
	Attachments []InvoicePartyDocument
}

// PaymentReadsHours is one InstructorHours of a listed payment.
type PaymentReadsHours struct {
	PaymentID     string  `db:"payment_id"`
	DocumentID    *string `db:"document_id"`
	DocumentToken *string `db:"document_token"`
}

// PaymentReadsTotals are payment_stats' aggregates (numeric text, nil for NULL).
type PaymentReadsTotals struct {
	Count           int64   `db:"total_transactions"`
	CurrentExpenses *string `db:"current_expenses"`
	CurrentIncome   *string `db:"current_income"`
	MonthlyExpenses *string `db:"monthly_expenses"`
	MonthlyToPay    *string `db:"monthly_to_pay"`
	MonthlyIncome   *string `db:"monthly_income"`
	MonthlyToCashIn *string `db:"monthly_to_cash_in"`
}

// PaymentReadsAccountChange is one values('custom_accounts__account_type').annotate(...) row.
type PaymentReadsAccountChange struct {
	Type *int64  `db:"account_type"`
	Net  *string `db:"net_change"`
}

// PaymentReadsSubscription is the subscription of subscription_payments.
type PaymentReadsSubscription struct {
	AssociationID   string  `db:"sport_association_id"`
	AssociateID     *string `db:"associate_id"`
	AssociateExists bool    `db:"associate_exists"`
	TaxCode         *string `db:"tax_code"`
}

// PaymentReadsLink is subscription_payments' course subscription or carnet of a course payment.
type PaymentReadsLink struct {
	Carnet        bool
	CourseID      *string
	Title         *string
	CourseDeleted bool
	Found         bool
}

type PaymentReadsStore interface {
	// UserAssociation is request.user.sportassociation.
	UserAssociation(ctx context.Context, user string) (*string, error)
	// ScopedAssociation is scoped_queryset(SportAssociation.objects).get(user=...) (live rows).
	ScopedAssociation(ctx context.Context, user string, scope *string) (*string, error)
	// StripeInstanceSettings reports whether the instance configuration overrides Stripe's settings.
	StripeInstanceSettings(ctx context.Context) (bool, error)
	Compressed(ctx context.Context, filter PaymentReadsFilter) ([]PaymentReadsCompressed, error)
	CountPayments(ctx context.Context, filter PaymentReadsFilter) (int64, error)
	// Payments returns the ordered page's payment ids (repeated when the joins repeat a payment).
	Payments(ctx context.Context, filter PaymentReadsFilter, limit, offset int64) ([]string, error)
	// Render loads PaymentOptimizedSerializer's data for the ids, with
	// get_subscription_id and get_course through the group-aware managers;
	// ErrPaymentReadsServer where Django raises.
	Render(ctx context.Context, ids []string, group *string) (map[string]PaymentReadsRender, error)
	Hours(ctx context.Context, ids []string) ([]PaymentReadsHours, error)
	Totals(ctx context.Context, filter PaymentReadsFilter) (PaymentReadsTotals, error)
	AccountChanges(ctx context.Context, filter PaymentReadsFilter) ([]PaymentReadsAccountChange, error)
	SubscriptionOf(ctx context.Context, id string, group, scope *string) (*PaymentReadsSubscription, error)
	Link(ctx context.Context, payment string, group, scope *string) (PaymentReadsLink, error)
}
