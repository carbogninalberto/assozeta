package domain

import (
	"context"
	"time"
)

// Rows document_invoice_view and document_einvoice_view
// (BE/docmanager/views/printing_views.py) read. JSON columns are their jsonb
// text (nil for SQL NULL); numeric columns their decimal text.

// InvoiceViewInvoice is the scoped, live, group-aware Invoice.
type InvoiceViewInvoice struct {
	ID, Association            string
	ActivityFee, MembershipFee string
	Description                *string
	Number                     *int64
	Cancelled                  bool
	SelectedTutor              *string
	CreationDate               time.Time
}

// InvoiceViewPayment is the invoice's scoped, live, group-aware Payment.
type InvoiceViewPayment struct {
	ID                    string
	Type                  string
	Subject               int64
	Description, Notes    *string
	PaymentDate           *time.Time
	CategoryID            *string
	MetaPaymentCategories *string
	AssociateID           *string
	SupplierID            *string
	InstructorID          *string
	Course                *string
	Meta                  *string
}

// InvoiceViewCategory is a PaymentCategory read by primary key (all rows).
type InvoiceViewCategory struct {
	Name          string
	TaxDeductible bool
	Deleted       bool
}

// InvoiceViewSupplier is a SupplierAndCustomers row (the foreign key's base manager).
type InvoiceViewSupplier struct {
	Name             string
	Address, TaxCode *string
}

// InvoiceViewOwner is the invoice association's settings and its user's flags.
type InvoiceViewOwner struct {
	ExtraText         *string
	InvoiceTemplate   *string
	EnumerateInvoices bool
	HideCategoryName  bool
	DefaultCategory   bool // user.default_payment_category is not None
}

// InvoiceViewSubscription is Payment.get_subscription with its association's fee plans.
type InvoiceViewSubscription struct {
	ID                string
	Start, End        *time.Time
	Type              int64
	Meta, CustomData  *string
	SubscriptionPlans *string
	MembershipPlans   *string
}

// InvoiceViewCourseName is what get_course_carnet_name found: a course,
// installment or carnet subscription paid by the payment (Found) with its
// title; MissingCarnet is a carnet subscription without a carnet.
type InvoiceViewCourseName struct {
	Found         bool
	MissingCarnet bool
	Title         *string
}

// InvoiceViewCustomerInvoice is a CustomerInvoice row (all fields
// generate_einvoice reads).
type InvoiceViewCustomerInvoice struct {
	Prefix                                                   *string
	Number, FiscalYear                                       *int64
	AssignorPrefixVAT                                        string
	AssignorVAT, AssignorTaxCode, AssignorDenomination       *string
	AssignorFiscalRegime                                     string
	AssignorAddress, AssignorPostalCode, AssignorCity        string
	AssignorProvince, AssignorCountry                        string
	AssignorEmail, AssignorPhone, AssignorFax                *string
	TransferorPrefixVAT                                      string
	TransferorVAT, TransferorTaxCode, TransferorDenomination *string
	TransferorFiscalRegime                                   string
	TransferorAddress, TransferorPostalCode, TransferorCity  string
	TransferorProvince, TransferorCountry                    string
	TransferorEmail, TransferorPhone, TransferorFax          *string
	CountryTransmitterPrefix                                 string
	IDTransmitter                                            *string
	TransmittingFormat, DocumentType, Currency               string
	TransmittingDate                                         *time.Time
	Causal                                                   *string
	Lines                                                    *string
	PaymentCondition, PaymentModality                        *string
	PaymentExpiryDate                                        time.Time
}

// InvoiceViewStore reads the rows of the invoice HTML views.
type InvoiceViewStore interface {
	MemberDocumentStore
	// Invoice is scoped_queryset(Invoice.objects).get(invoice_id=id).
	Invoice(ctx context.Context, id string, scope RenderScope) (*InvoiceViewInvoice, error)
	// Payments is scoped_queryset(Payment.objects).filter(invoice=id)[:21] (get()).
	Payments(ctx context.Context, invoice string, scope RenderScope) ([]InvoiceViewPayment, error)
	Owner(ctx context.Context, association string) (*InvoiceViewOwner, error)
	Category(ctx context.Context, id string) (*InvoiceViewCategory, error)
	Supplier(ctx context.Context, id string) (*InvoiceViewSupplier, error)
	AssociateMinor(ctx context.Context, id string) (bool, error)
	// PaymentSubscription is Payment.get_subscription: the subscription id or nil.
	PaymentSubscription(ctx context.Context, payment string, group *string) (*string, error)
	Subscription(ctx context.Context, id string) (*InvoiceViewSubscription, error)
	// MembershipPeriod is SubscriptionMembership.objects.filter(subscription=...).first()
	// (subscription nil: the rows without one); found false without a row.
	MembershipPeriod(ctx context.Context, subscription *string) (start, end *time.Time, found bool, err error)
	CourseName(ctx context.Context, payment string, group *string) (InvoiceViewCourseName, error)
	CustomerInvoice(ctx context.Context, id string) (*InvoiceViewCustomerInvoice, error)
}
