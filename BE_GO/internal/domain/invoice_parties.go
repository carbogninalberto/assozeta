package domain

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// Invoice parties: customer and supplier invoice list, add and update
// (invoice_customers_* and invoice_suppliers_* in invoice_views.py).

var (
	// ErrInvoicePartyNotPorted refuses behaviour the Go port does not carry
	// (XML/PDF generation, print tasks, nested payment or supplier writes),
	// always before any write.
	ErrInvoicePartyNotPorted = refusal.New("invoice party behaviour not ported")
	// ErrInvoicePartyNotAssociation is User.sport_association's
	// PermissionDenied('User is not a sport association') for athletes.
	ErrInvoicePartyNotAssociation = errors.New("User is not a sport association")
	// ErrInvoicePartyMissing is the views' bare Exception (500): no scoped
	// association or no invoice of the association.
	ErrInvoicePartyMissing = errors.New("invoice party not found")
	// ErrInvoicePartyServer is a Python error the view raises (TypeError,
	// KeyError, ValueError, a rejected model value): the 500 page.
	ErrInvoicePartyServer = errors.New("invoice party server error")
	// ErrInvoicePartyForeignReference is the tenant scope's generic 403.
	ErrInvoicePartyForeignReference = errors.New("invoice party foreign reference")
	// ErrInvoicePartyOrdering is order_by() on a name the model does not have.
	ErrInvoicePartyOrdering = errors.New("invoice party ordering field")
)

// InvoicePartyResponseError is a Response({'error': ...}, status) of the views.
type InvoicePartyResponseError struct {
	Status  int
	Message string
}

func (e *InvoicePartyResponseError) Error() string { return e.Message }

// CustomerInvoiceRow is one CustomerInvoice with the PDF token it renders.
type CustomerInvoiceRow struct {
	ID                        string     `db:"customer_invoice_id"`
	Prefix                    *string    `db:"prefix"`
	Number                    *int32     `db:"number"`
	FiscalYear                *int32     `db:"fiscal_year"`
	AssignorPrefixVATNumber   string     `db:"assignor_prefix_vat_number"`
	AssignorVATNumber         *string    `db:"assignor_vat_number"`
	AssignorTaxCode           *string    `db:"assignor_tax_code"`
	AssignorDenomination      *string    `db:"assignor_denomination"`
	AssignorFiscalRegime      string     `db:"assignor_fiscal_regime"`
	AssignorAddress           string     `db:"assignor_address"`
	AssignorPostalCode        string     `db:"assignor_postal_code"`
	AssignorCity              string     `db:"assignor_city"`
	AssignorProvince          string     `db:"assignor_province"`
	AssignorCountry           string     `db:"assignor_country"`
	AssignorContactEmail      *string    `db:"assignor_contact_email"`
	AssignorContactPhone      *string    `db:"assignor_contact_phone"`
	AssignorFax               *string    `db:"assignor_fax"`
	TransferorPrefixVATNumber string     `db:"transferor_prefix_vat_number"`
	TransferorVATNumber       *string    `db:"transferor_vat_number"`
	TransferorTaxCode         *string    `db:"transferor_tax_code"`
	TransferorDenomination    *string    `db:"transferor_denomination"`
	TransferorFiscalRegime    string     `db:"transferor_fiscal_regime"`
	TransferorAddress         string     `db:"transferor_address"`
	TransferorPostalCode      string     `db:"transferor_postal_code"`
	TransferorCity            string     `db:"transferor_city"`
	TransferorProvince        string     `db:"transferor_province"`
	TransferorCountry         string     `db:"transferor_country"`
	TransferorContactEmail    *string    `db:"transferor_contact_email"`
	TransferorContactPhone    *string    `db:"transferor_contact_phone"`
	TransferorFax             *string    `db:"transferor_fax"`
	EInvoice                  bool       `db:"e_invoice"`
	CountryTransmitterPrefix  string     `db:"country_transmitter_prefix"`
	IDTransmitter             *string    `db:"id_transmitter"`
	TransmittingFormat        string     `db:"transmitting_format"`
	DocumentType              string     `db:"document_type"`
	Currency                  string     `db:"currency"`
	TransmittingDate          *time.Time `db:"transmitting_date"`
	Causal                    *string    `db:"causal"`
	Lines                     *string    `db:"lines"`
	PaymentCondition          *string    `db:"payment_condition"`
	PaymentModality           *string    `db:"payment_modality"`
	PaymentTotalAmount        string     `db:"payment_total_amount"`
	PaymentExpiryDate         time.Time  `db:"payment_expiry_date"`
	XML                       *string    `db:"xml"`
	JSON                      *string    `db:"json"`
	CreationDate              time.Time  `db:"creation_date"`
	Paid                      bool       `db:"paid"`
	Transmitted               bool       `db:"transmitted"`
	PDFID                     *string    `db:"pdf_id"`
	AssociationID             string     `db:"sport_association_id"`
	PaymentID                 *string    `db:"payment_id"`
	PDFToken                  *string    `db:"pdf_token"`
}

// InvoicePartyDocument is one Document as DocumentSerializer renders it.
type InvoicePartyDocument struct {
	ID           string    `db:"document_id"`
	CreationDate time.Time `db:"creation_date"`
	Filepath     *string   `db:"filepath"`
	Filename     string    `db:"filename"`
	Size         *int64    `db:"file_size_bytes"`
	Token        string    `db:"token"`
}

// InvoicePartyUser is UserSerializer's columns.
type InvoicePartyUser struct {
	FirstName string  `db:"first_name"`
	LastName  string  `db:"last_name"`
	Email     string  `db:"email"`
	Phone     *string `db:"phone"`
	Username  string  `db:"username"`
	Role      int     `db:"role"`
}

// InvoicePartyPayment is one Payment with what PaymentSerializer renders.
type InvoicePartyPayment struct {
	ID                    string     `db:"payment_id"`
	Type                  string     `db:"type"`
	Subject               int        `db:"subject"`
	Amount                string     `db:"amount"`
	CreationDate          time.Time  `db:"creation_date"`
	PaymentDate           *time.Time `db:"payment_date"`
	Paid                  bool       `db:"paid"`
	Expense               bool       `db:"expense"`
	Description           *string    `db:"description"`
	CategoryID            *string    `db:"payment_category_id"`
	MetaPaymentCategories *string    `db:"meta_payment_categories"`
	InvoiceID             *string    `db:"invoice_id"`
	UserID                *string    `db:"user_id"`
	AssociateID           *string    `db:"associate_id"`
	SupplierID            *string    `db:"supplier_id"`
	InstructorID          *string    `db:"instructor_id"`
	AssociationID         *string    `db:"sport_association_id"`
	PaymentIntentID       *string    `db:"payment_intent_id"`
	Archived              bool       `db:"archived"`
	InvoiceFiscalCode     *string    `db:"invoice_fiscal_code"`
	Meta                  *string    `db:"meta"`
	Notes                 *string    `db:"notes"`
	AccountID             *string    `db:"custom_accounts_id"`
	SignatureID           *string    `db:"signature_id"`
	Deleted               bool       `db:"deleted"`
	GroupID               *string    `db:"group_id"`
	Course                *string    `db:"course"`
	AccountType           *int       `db:"account_type"`
	AccountName           *string    `db:"account_name"`
	CategoryName          *string    `db:"category_name"`

	User         *InvoicePartyUser
	Supplier     *Supplier
	Attachments  []InvoicePartyDocument
	Subscription *string
}

// SupplierInvoiceRow is one InvoiceSuppliers with its rendered relations.
type SupplierInvoiceRow struct {
	ID            string     `db:"invoice_supplier_id"`
	Identifier    string     `db:"invoice_identifier"`
	Amount        string     `db:"amount"`
	PaymentDate   *time.Time `db:"payment_date"`
	ExpireDate    *time.Time `db:"expire_date"`
	AssociationID string     `db:"sport_association_id"`
	PaymentID     *string    `db:"payment_id"`
	Paid          bool       `db:"paid"`
	Notes         *string    `db:"notes"`
	DocumentID    *string    `db:"document_pdf_id"`
	SupplierID    *string    `db:"supplier_id"`
	DocumentToken *string    `db:"document_token"`

	Payment  *InvoicePartyPayment
	Supplier *Supplier
}

// SupplierInvoiceQuery is invoice_suppliers_list's filtered, ordered page.
type SupplierInvoiceQuery struct {
	AssociationID string
	// AmountText/AmountExact search the amount (a finite float); Text the
	// text columns. At most one of them is set.
	AmountText  *string
	AmountExact *string
	Text        *string
	OrderColumn string
	Descending  bool
}

// InvoicePartyAudit is one auditlog entry with its index resolution.
type InvoicePartyAudit struct {
	Model, Key     string
	Action         int
	Changes        map[string][2]string
	AssociationID  *string
	ResolutionPath *string
}

// NewSupplierPayment is Payment.objects.create(**validated) of the add view.
type NewSupplierPayment struct {
	ID, Amount, Description, CategoryID, AccountID string
	CreationDate                                   time.Time
	PaymentDate                                    *time.Time
	Paid                                           bool
	GroupID                                        *string
}

// NewSupplierInvoice is InvoiceSuppliers.objects.create(**validated).
type NewSupplierInvoice struct {
	ID, Identifier, Amount, AssociationID string
	PaymentDate, ExpireDate               *time.Time
	Paid                                  bool
	Notes                                 *string
	DocumentID                            *string
}

type InvoicePartyStore interface {
	// CustomerInvoicesWithoutPDF is CustomerInvoice.objects.filter(sport_association=association, pdf__isnull=True).exists().
	CustomerInvoicesWithoutPDF(ctx context.Context, association string) (bool, error)
	// OwnerAssociation is SportAssociation.objects.get(user=owner).
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	// ScopedAssociation is scoped_queryset(SportAssociation.objects).filter(user=...).first().
	ScopedAssociation(ctx context.Context, owner string, impersonation *string) (*string, error)
	CustomerInvoices(ctx context.Context, association string) ([]CustomerInvoiceRow, error)
	CustomerInvoice(ctx context.Context, id, association string) (*CustomerInvoiceRow, error)
	CountSupplierInvoices(ctx context.Context, query SupplierInvoiceQuery) (int64, error)
	SupplierInvoices(ctx context.Context, query SupplierInvoiceQuery, limit, offset int64, group *string) ([]SupplierInvoiceRow, error)
	SupplierInvoice(ctx context.Context, id, association string) (*SupplierInvoiceRow, error)
	// Payment loads one payment through the base manager (FK access), with
	// visible telling whether Payment.objects (live, group) sees it.
	Payment(ctx context.Context, id string, group *string) (payment *InvoicePartyPayment, visible bool, err error)

	// Validation lookups.
	Exists(ctx context.Context, table, key, id string) (bool, error)
	// PaymentAssociation is the live, group-visible payment's association.
	PaymentAssociation(ctx context.Context, id string, group *string) (found bool, association *string, err error)
	CustomerInvoiceTaken(ctx context.Context, prefix string, number, fiscalYear int64, association string) (bool, error)
	CustomerNumberTaken(ctx context.Context, number, fiscalYear *int64) (bool, error)
	BankAccount(ctx context.Context, association string, id *string) (*string, error)
	LiveSupplier(ctx context.Context, id, association string, group *string) (*Supplier, error)
	SupplierPaymentCategory(ctx context.Context, association string) (*string, error)
	DefaultPaymentCategory(ctx context.Context, association *string) (*string, error)
	ActorAssociation(ctx context.Context, actor string) (*string, error)

	// Writes, each its own transaction (Django autocommit).
	// CreateSupplierPayment inserts the payment, audits the create (changes
	// from the stored row) and the save that sets owner, association and supplier.
	CreateSupplierPayment(ctx context.Context, payment NewSupplierPayment, created, saved InvoicePartyAudit,
		owner, association, supplier string, audit LoginAuditContext, at time.Time) error
	CreateSupplierInvoice(ctx context.Context, invoice NewSupplierInvoice, created, linked InvoicePartyAudit,
		payment, supplier string, audit LoginAuditContext, at time.Time) error
	AppendAudit(ctx context.Context, entry InvoicePartyAudit, audit LoginAuditContext, at time.Time) error
	UpdateCustomerInvoice(ctx context.Context, id string, paid, transmitted bool) error
	UpdateSupplierInvoice(ctx context.Context, row SupplierInvoiceRow) error
	UpdatePaymentPaid(ctx context.Context, id string, paid bool, at time.Time, category, description *string) error
}
