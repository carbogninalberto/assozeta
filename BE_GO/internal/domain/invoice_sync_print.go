package domain

import (
	"context"
	"time"
)

// InvoiceSyncRow is what invoice_update reads and assigns on the invoice.
type InvoiceSyncRow struct {
	ID              string  `db:"invoice_id"`
	AssociationID   string  `db:"sport_association_id"`
	Number          *int64  `db:"number"`
	Cancelled       bool    `db:"cancelled"`
	SelectedTutorID *string `db:"selected_tutor_id"`
	Description     *string `db:"description"`
}

// InvoiceSyncSave is invoice.save() of invoice_update (the assigned fields).
type InvoiceSyncSave struct {
	ID              string
	Number          int64
	Cancelled       bool
	SelectedTutorID *string
	Description     *string
}

// InvoiceSyncTransaction is the request's payment-side work: the reads before
// the payment save, the save itself and generate_invoice_description.
type InvoiceSyncTransaction interface {
	InvoiceGenerateTransaction
	// SyncInvoice is scoped_queryset(Invoice.objects).filter(invoice_id=id,
	// sport_association=association, archived=False).first() (live, X-Group-Id aware).
	SyncInvoice(ctx context.Context, id, association string, scope, group *string) (*InvoiceSyncRow, error)
	// SyncAssociate is scoped_queryset(Associate.objects).filter(associate_id=id).first() is not None.
	SyncAssociate(ctx context.Context, id string, scope, group *string) (bool, error)
	// InvoicePayment is scoped_queryset(Payment.objects).filter(invoice=invoice).first() (by pk), locked.
	InvoicePayment(ctx context.Context, invoice string, scope, group *string) (*PaymentStateRow, error)
	// SaveInvoicePayment writes what payment.save() changes in invoice_update.
	SaveInvoicePayment(ctx context.Context, id string, paymentDate *time.Time, category, description *string) error
}

// InvoiceSyncStore persists invoice_update; each method after the payment
// transaction is its own autocommit step.
type InvoiceSyncStore interface {
	BeginInvoiceSync(ctx context.Context) (InvoiceSyncTransaction, error)
	// AppendInvoiceAudit is auditlog's pre_save update entry of the invoice.
	AppendInvoiceAudit(ctx context.Context, invoice, association string, changes map[string][2]string, audit LoginAuditContext,
		at time.Time) error
	SaveSyncInvoice(ctx context.Context, save InvoiceSyncSave) error
	// RenderInvoice is the row InvoiceSerializer renders.
	RenderInvoice(ctx context.Context, id string) (*PaymentReadsInvoice, error)
}

// NewCustomerInvoice is CustomerInvoice.objects.create(**validated_data) of
// invoice_customers_add: every column, defaults applied.
type NewCustomerInvoice struct {
	ID                                                                      string
	Prefix                                                                  *string
	Number, FiscalYear                                                      *int64
	AssignorPrefixVAT                                                       string
	AssignorVAT, AssignorTaxCode, AssignorDenomination                      *string
	AssignorFiscalRegime, AssignorAddress, AssignorPostalCode, AssignorCity string
	AssignorProvince, AssignorCountry                                       string
	AssignorEmail, AssignorPhone, AssignorFax                               *string
	TransferorPrefixVAT                                                     string
	TransferorVAT, TransferorTaxCode, TransferorDenomination                *string
	TransferorFiscalRegime, TransferorAddress, TransferorPostalCode         string
	TransferorCity, TransferorProvince, TransferorCountry                   string
	TransferorEmail, TransferorPhone, TransferorFax                         *string
	EInvoice                                                                bool
	CountryTransmitterPrefix                                                string
	IDTransmitter                                                           *string
	TransmittingFormat, DocumentType, Currency                              string
	TransmittingDate                                                        time.Time
	Causal                                                                  *string
	PaymentCondition, PaymentModality                                       *string
	PaymentTotalAmount                                                      string // numeric text
	PaymentExpiryDate                                                       time.Time
	XML                                                                     *string
	PDF, Payment                                                            *string
	Association                                                             string
	CreationDate                                                            time.Time
	Paid, Transmitted                                                       bool
	// Lines and JSON are the JSON texts psycopg sends (json.dumps); nil is SQL NULL.
	Lines, JSON *string
}

// CustomerInvoiceSyncStore persists the valid add (each method one autocommit step).
type CustomerInvoiceSyncStore interface {
	// CreateCustomerInvoice inserts the row and its create entry; overrides
	// replace the stored rendering of fields whose in-memory str() differs.
	CreateCustomerInvoice(ctx context.Context, invoice NewCustomerInvoice, overrides map[string]string, audit LoginAuditContext,
		at time.Time) error
	// SaveCustomerInvoice is invoice.save(): the update entry of the fields
	// whose in-memory rendering (memory) differs from the stored row, then
	// the xml and json columns when set.
	SaveCustomerInvoice(ctx context.Context, id, association string, memory map[string]string, xml, json *string,
		audit LoginAuditContext, at time.Time) error
}

// PendingCustomerInvoice is a CustomerInvoice without a PDF, as
// invoice_customers_list's first loop reads it.
type PendingCustomerInvoice struct {
	ID, Association string
	Row             InvoiceViewCustomerInvoice
}

// CustomerInvoicePendingStore backs invoice_customers_list's first loop.
type CustomerInvoicePendingStore interface {
	// PendingCustomerInvoices is CustomerInvoice.objects.filter(pdf__isnull=True), in the database's order.
	PendingCustomerInvoices(ctx context.Context) ([]PendingCustomerInvoice, error)
	// SaveCustomerInvoiceXML is invoice.save() after the XML changed: the
	// update entry of xml (when it differs) and the column.
	SaveCustomerInvoiceXML(ctx context.Context, id, association, xml string, audit LoginAuditContext, at time.Time) error
}
