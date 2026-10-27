package domain

import (
	"context"
	"time"
)

type SupplierInvoiceStatRow struct {
	Amount       string     `db:"amount"`
	Paid         bool       `db:"paid"`
	ExpireDate   *time.Time `db:"expire_date"`
	Identifier   string     `db:"invoice_identifier"`
	SupplierName *string    `db:"supplier_name"`
}

type SupplierInvoiceStatsStore interface {
	// ScopedAssociation is scoped_queryset(SportAssociation.objects).get(user=user).
	ScopedAssociation(ctx context.Context, user string, scope *string) (string, error)
	// SupplierInvoices are ordered -expire_date (NULLs first, as PostgreSQL sorts DESC).
	SupplierInvoices(ctx context.Context, association string) ([]SupplierInvoiceStatRow, error)
}
