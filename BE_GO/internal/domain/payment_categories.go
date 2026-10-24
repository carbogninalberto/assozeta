package domain

import (
	"context"
	"errors"
	"time"
)

var (
	ErrPaymentCategoryForbidden     = errors.New("payment category role not allowed")
	ErrPaymentCategoryListForbidden = errors.New("payment category list role not allowed")
	ErrPaymentCategoryNotFound      = errors.New("payment category not found")
	ErrPaymentCategoryInvalidID     = errors.New("payment category identifier is invalid")
	ErrPaymentCategoryVATRequired   = errors.New("payment category VAT is required")
)

type PaymentCategoryVAT struct {
	ID        string `db:"vat_management_id"`
	VAT       uint16 `db:"vat"`
	Deduction uint16 `db:"deduction"`
}

type PaymentCategoryState struct {
	ID            string    `db:"payment_category_id"`
	Name          string    `db:"name"`
	CreationDate  time.Time `db:"creation_date"`
	AssociationID *string   `db:"sport_association_id"`
	Expense       bool      `db:"expense"`
	TaxDeductible bool      `db:"tax_deductible"`
	Archived      bool      `db:"archived"`
	Type          uint16    `db:"type"`
	Deleted       bool      `db:"deleted"`
	VAT           *PaymentCategoryVAT
}

type PaymentCategoryAudit struct {
	Model, ID, ObjectRepresentation string
	Action                          int
	Changes                         map[string][2]string
	Context                         LoginAuditContext
	AssociationID, ResolutionPath   *string
	At                              time.Time
}

type PaymentCategoryReader interface {
	ListPaymentCategories(context.Context, string, *string) ([]PaymentCategoryState, error)
}

type PaymentCategoryStore interface {
	PaymentCategoryReader
	BeginPaymentCategories(context.Context) (PaymentCategoryTransaction, error)
}

type PaymentCategoryTransaction interface {
	OwnerForUpdate(context.Context, string) (int, bool, bool, error)
	AssociationForOwner(context.Context, string, *string) (string, error)
	AssociationForActor(context.Context, string) (string, error)
	AssociationExists(context.Context, string) (bool, error)
	PaymentCategoryIDExists(context.Context, string) (bool, error)
	CategoryForUpdate(context.Context, string, string) (PaymentCategoryState, error)
	VATByID(context.Context, string) (*PaymentCategoryVAT, error)
	CreateVAT(context.Context, PaymentCategoryVAT) error
	InsertPaymentCategory(context.Context, PaymentCategoryState) error
	UpdatePaymentCategory(context.Context, PaymentCategoryState) error
	AppendPaymentCategoryAudit(context.Context, PaymentCategoryAudit) error
	Commit() error
	Rollback() error
}
