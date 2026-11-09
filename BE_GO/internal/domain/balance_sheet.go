package domain

import (
	"context"
	"errors"
	"time"
)

// BalanceSheetRecord is one stored BalanceSheet row; Data is the jsonb text
// (nil for SQL NULL).
type BalanceSheetRecord struct {
	ID            string    `db:"balance_sheet_id"`
	AssociationID *string   `db:"sport_association_id"`
	StatusFlag    int       `db:"status_flag"`
	CreationDate  time.Time `db:"creation_date"`
	Year          int       `db:"year"`
	Data          *string   `db:"data"`
	Archived      bool      `db:"archived"`
}

// BalanceSheetPayment is one payment generate_balance_sheet iterates, with
// its select_related category. Meta is the meta_payment_categories jsonb text.
type BalanceSheetPayment struct {
	Amount       string  `db:"amount"`
	Expense      bool    `db:"expense"`
	Meta         *string `db:"meta"`
	CategoryID   *string `db:"category_id"`
	CategoryName *string `db:"category_name"`
	CategoryType *int    `db:"category_type"`
}

type BalanceSheetCategory struct {
	ID   string `db:"payment_category_id"`
	Name string `db:"name"`
	Type int    `db:"type"`
}

// ErrBalanceSheetSaveFailed wraps a Model.save() UPDATE that failed after
// auditlog's pre_save entry was written.
var ErrBalanceSheetSaveFailed = errors.New("balance sheet save failed after its audit entry")

type BalanceSheetWriteTransaction interface {
	// SheetAssociation is request.user.sportassociation (the reverse
	// one-to-one accessor, base manager); ErrAssociationNotFound when absent.
	SheetAssociation(ctx context.Context, owner string) (string, error)
	StartDate(ctx context.Context, user string) (day, month int64, err error)
	// SheetOfYear is the association's newest sheet of the year; nil when absent.
	SheetOfYear(ctx context.Context, association string, year int) (*BalanceSheetRecord, error)
	// NewestSheet is the association's newest sheet of any year; nil when absent.
	NewestSheet(ctx context.Context, association string) (*BalanceSheetRecord, error)
	// SheetPayments are generate_balance_sheet's payments, unordered.
	SheetPayments(ctx context.Context, association string, from, to time.Time, group *string) ([]BalanceSheetPayment, error)
	// Category is PaymentCategory.objects.filter(pk=id).first().
	Category(ctx context.Context, id string) (*BalanceSheetCategory, error)
	// AccountBalances are get_current_balance_from(1970-01-01, to) of the
	// association's accounts of one type (numeric text), unordered.
	AccountBalances(ctx context.Context, association string, kind int, to time.Time, group *string) ([]string, error)
	// InsertSheet is objects.create with its auditlog create entry; it returns
	// the stored data text.
	InsertSheet(ctx context.Context, row BalanceSheetRecord, audit LoginAuditContext, at time.Time) (*string, error)
	// SaveSheet is Model.save(): the update entry (when changes is not empty)
	// first, then the UPDATE of every field; an UPDATE failure is
	// ErrBalanceSheetSaveFailed and leaves the entry in place.
	SaveSheet(ctx context.Context, row BalanceSheetRecord, changes map[string][2]string, audit LoginAuditContext, at time.Time) error
	// AuditSheetUpdate writes only the update entry (the save failed before
	// its UPDATE).
	AuditSheetUpdate(ctx context.Context, row BalanceSheetRecord, changes map[string][2]string, audit LoginAuditContext, at time.Time) error
	Commit() error
	Rollback() error
}

type BalanceSheetWriteStore interface {
	BeginBalanceSheetWrite(context.Context) (BalanceSheetWriteTransaction, error)
}

type BalanceAccountDeleteTransaction interface {
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	AccountForUpdate(ctx context.Context, id, association string) (*BalanceAccountRow, error)
	// AccountHasPayments reports any payment row referencing the account
	// (the deletion collector uses the base manager).
	AccountHasPayments(ctx context.Context, id string) (bool, error)
	// DeleteAccount deletes the account's transfers, then the account, each
	// with its auditlog delete entry.
	DeleteAccount(ctx context.Context, id string, audit LoginAuditContext, at time.Time) error
	Commit() error
	Rollback() error
}

type BalanceAccountDeleteStore interface {
	BeginBalanceAccountDelete(context.Context) (BalanceAccountDeleteTransaction, error)
}
