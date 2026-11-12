package domain

import (
	"context"
	"errors"
	"time"
)

// ErrInvoiceDeleteMissing is the view's raise Exception('Invoice not found') (500).
var ErrInvoiceDeleteMissing = errors.New("Invoice not found")

type InvoiceDeleteKind struct {
	Table, Key, Model, Representation string
}

var (
	SupplierInvoiceDelete = InvoiceDeleteKind{Table: "application_invoicesuppliers", Key: "invoice_supplier_id",
		Model: "invoicesuppliers", Representation: "InvoiceSuppliers"}
	CustomerInvoiceDelete = InvoiceDeleteKind{Table: "application_customerinvoice", Key: "customer_invoice_id",
		Model: "customerinvoice", Representation: "CustomerInvoice"}
)

type InvoiceDeleteStore interface {
	BeginInvoiceDelete(context.Context) (InvoiceDeleteTransaction, error)
}

type InvoiceDeleteTransaction interface {
	AssociationForOwner(context.Context, string, *string) (string, error)
	// DeleteInvoice deletes the association's invoice and appends its audit entry.
	DeleteInvoice(ctx context.Context, kind InvoiceDeleteKind, id, association string, audit LoginAuditContext, at time.Time) error
	Commit() error
	Rollback() error
}
