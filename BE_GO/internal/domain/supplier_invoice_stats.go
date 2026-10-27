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

type CustomerInvoiceStatRow struct {
	Amount     string    `db:"payment_total_amount"`
	Paid       bool      `db:"paid"`
	ExpiryDate time.Time `db:"payment_expiry_date"`
	Prefix     *string   `db:"prefix"`
	Number     *int64    `db:"number"`
	FiscalYear *int64    `db:"fiscal_year"`
}

type CustomerInvoiceStatsStore interface {
	ScopedAssociation(ctx context.Context, user string, scope *string) (string, error)
	// CustomerInvoices are ordered -payment_expiry_date.
	CustomerInvoices(ctx context.Context, association string) ([]CustomerInvoiceStatRow, error)
}
