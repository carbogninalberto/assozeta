package domain

import (
	"context"
	"errors"
	"time"
)

var ErrPaymentNotFound = errors.New("payment not found")

// PaymentSaveRow is a locked payment with the columns Payment.save() reads.
type PaymentSaveRow struct {
	ID            string  `db:"payment_id"`
	AssociationID string  `db:"sport_association_id"`
	Subject       int     `db:"subject"`
	Archived      bool    `db:"archived"`
	CategoryID    *string `db:"payment_category_id"`
	CategoryName  *string `db:"payment_category_name"`
	Description   *string `db:"description"`
	Meta          *string `db:"meta"`
	Course        *string `db:"course"`
	// MetaPaymentCategories is the third JSON column Payment.save() reads.
	// Queries select it as text, like meta and course.
	MetaPaymentCategories *string `db:"meta_payment_categories"`
	// CustomAccounts is whether custom_accounts is set; nil unless the query
	// selects it.
	CustomAccounts *bool `db:"custom_accounts_set"`
	// JSONDepth is the deepest nesting (dto.AuditJSONDepth) of the three JSON
	// texts above, 0 for NULL. The save hook fills it in.
	JSONDepth int `db:"-"`
	// IndexFallback is whether the audit index resolver cannot reload this row
	// and so resolves the association from the log entry instead.
	IndexFallback bool `db:"-"`
}

// PaymentLinkedName is get_course_carnet_name's linked row: the first course
// subscription, else installment (both including soft-deleted rows), else
// carnet subscription paid by the payment, with its course or carnet title.
type PaymentLinkedName struct {
	Found bool
	// Slot is the relation kind (course subscription, installment, carnet).
	Slot          int
	Title         *string
	MissingCarnet bool
}

type PaymentArchiveEvent struct {
	PaymentID     string
	AssociationID string
	Changes       map[string][2]string
	Context       LoginAuditContext
	At            time.Time
}

type PaymentArchiveStore interface {
	BeginPaymentArchive(context.Context) (PaymentArchiveTransaction, error)
}

type PaymentArchiveTransaction interface {
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	// PaymentForArchive locks a live payment of the association (group-aware).
	PaymentForArchive(ctx context.Context, id, association string, group *string) (PaymentSaveRow, error)
	DefaultPaymentCategory(ctx context.Context, association *string) (id, name *string, err error)
	PaymentLinkedName(ctx context.Context, payment string) (PaymentLinkedName, error)
	SavePaymentArchive(ctx context.Context, id string, archived bool, category, description *string) error
	AppendPaymentAudit(ctx context.Context, event PaymentArchiveEvent) error
	Commit() error
	Rollback() error
}
