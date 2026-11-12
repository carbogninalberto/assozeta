package dto

import "github.com/carbogninalberto/assozeta/BE_GO/internal/domain"

// SupplierItem is SupplierSerializer (fields '__all__').
type SupplierItem struct {
	ID            string  `json:"supplier_id"`
	Name          string  `json:"name"`
	Address       *string `json:"address"`
	TaxCode       *string `json:"tax_code"`
	AssociationID *string `json:"sport_association"`
	VATNumber     *string `json:"vat_number"`
	Type          *string `json:"type"`
	Email         *string `json:"email"`
	PhoneNumber   *string `json:"phone_number"`
	City          *string `json:"city"`
	Cap           *string `json:"cap"`
	State         *string `json:"state"`
	Country       *string `json:"country"`
	Nationality   *string `json:"nationality"`
	Note          *string `json:"note"`
	GroupID       *string `json:"group"`
	Deleted       bool    `json:"deleted"`
}

type SupplierPageMeta struct {
	Total   int64 `json:"total"`
	Page    int64 `json:"page"`
	Pages   int64 `json:"pages"`
	PerPage int64 `json:"perpage"`
}

func SupplierItemFrom(row domain.Supplier) SupplierItem {
	return SupplierItem(row)
}
