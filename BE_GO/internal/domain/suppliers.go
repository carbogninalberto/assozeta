package domain

import (
	"context"
	"errors"
)

// Supplier is one SupplierAndCustomers row with the columns SupplierSerializer renders.
type Supplier struct {
	ID            string  `db:"supplier_id"`
	Name          string  `db:"name"`
	Address       *string `db:"address"`
	TaxCode       *string `db:"tax_code"`
	AssociationID *string `db:"sport_association_id"`
	VATNumber     *string `db:"vat_number"`
	Type          *string `db:"type"`
	Email         *string `db:"email"`
	PhoneNumber   *string `db:"phone_number"`
	City          *string `db:"city"`
	Cap           *string `db:"cap"`
	State         *string `db:"state"`
	Country       *string `db:"country"`
	Nationality   *string `db:"nationality"`
	Note          *string `db:"note"`
	GroupID       *string `db:"group_id"`
	Deleted       bool    `db:"deleted"`
}

// SupplierQuery is the manager's live, optionally group-filtered rows of one
// association, an optional icontains search over every text field and an
// optional ordering column.
type SupplierQuery struct {
	AssociationID string
	GroupID       *string
	Search        string
	OrderColumn   string
	Descending    bool
	Random        bool
}

var ErrSupplierNotFound = errors.New("supplier not found")

type SupplierStore interface {
	// OwnerAssociation is SportAssociation.objects.get(user=owner).
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	CountSuppliers(ctx context.Context, query SupplierQuery) (int64, error)
	// Suppliers returns a page (limit < 0 means every row).
	Suppliers(ctx context.Context, query SupplierQuery, limit, offset int64) ([]Supplier, error)
	Supplier(ctx context.Context, association, id string, group *string) (Supplier, error)
}
