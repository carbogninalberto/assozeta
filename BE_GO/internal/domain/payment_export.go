package domain

import (
	"context"
	"time"
)

// PaymentExportAssociation is the scoped live association of the exporting user.
type PaymentExportAssociation struct {
	ID           string  `db:"sport_association_id"`
	Denomination *string `db:"denomination"`
}

// PaymentExportRow is what payment_list_export reads of one payment (forward
// relations through the base managers).
type PaymentExportRow struct {
	ID              string     `db:"payment_id"`
	Type            string     `db:"type"`
	Subject         int64      `db:"subject"`
	Amount          string     `db:"amount"`
	CreationDate    time.Time  `db:"creation_date"`
	PaymentDate     *time.Time `db:"payment_date"`
	Paid            bool       `db:"paid"`
	Expense         bool       `db:"expense"`
	Description     *string    `db:"description"`
	MetaCategories  *string    `db:"meta_payment_categories"`
	Notes           *string    `db:"notes"`
	Course          *string    `db:"course"`
	AccountID       *string    `db:"custom_accounts_id"`
	AccountName     *string    `db:"account_name"`
	HasCategory     bool       `db:"has_category"`
	CategoryName    *string    `db:"category_name"`
	HasInvoice      bool       `db:"has_invoice"`
	InvoiceNumber   *int64     `db:"invoice_number"`
	HasAssociate    bool       `db:"has_associate"`
	AssociateFirst  *string    `db:"associate_first_name"`
	AssociateLast   *string    `db:"associate_last_name"`
	HasInstructor   bool       `db:"has_instructor"`
	InstructorFirst *string    `db:"instructor_first_name"`
	InstructorLast  *string    `db:"instructor_last_name"`
	HasSupplier     bool       `db:"has_supplier"`
	SupplierName    *string    `db:"supplier_name"`
	// Transfer marks the petty cash book's pseudo payment of an account transfer.
	Transfer bool `db:"-"`
}

// PaymentExportTransfer is one CustomAccountsTransfer of the window.
type PaymentExportTransfer struct {
	ID       string    `db:"custom_account_transfer_id"`
	Amount   string    `db:"amount"`
	Date     time.Time `db:"date"`
	FromID   *string   `db:"custom_account_from_id"`
	FromName *string   `db:"from_name"`
	ToID     *string   `db:"custom_account_to_id"`
	ToName   *string   `db:"to_name"`
}

// PaymentExportCourse is the course column's related title (Found: a course
// subscription or installment exists; Title may be NULL).
type PaymentExportCourse struct {
	Found bool
	Title *string
}

type PaymentExportStore interface {
	// ScopedAssociation is scoped_queryset(SportAssociation.objects).get(user=...) (nil when missing).
	ScopedAssociation(ctx context.Context, user string, scope *string) (*PaymentExportAssociation, error)
	// Payments are the filtered payments ordered by -payment_date.
	Payments(ctx context.Context, filter PaymentReadsFilter) ([]PaymentExportRow, error)
	// Courses resolves each payment's first course subscription (any state),
	// else first installment (any state, group-aware) course title.
	Courses(ctx context.Context, ids []string, group, scope *string) (map[string]PaymentExportCourse, error)
	// Transfers are the association's account transfers dated within [from, to].
	Transfers(ctx context.Context, association string, from, to time.Time) ([]PaymentExportTransfer, error)
	// Balances is get_current_balance_from(1970-01-01, to) of each account (numeric text).
	Balances(ctx context.Context, accounts []string, to time.Time, group *string) (map[string]string, error)
	// CategoryNames are the association's and the global payment categories by id.
	CategoryNames(ctx context.Context, association string) (map[string]string, error)
}
