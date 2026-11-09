package domain

import (
	"context"
	"errors"
	"time"
)

var (
	// ErrMiscExportsServer is an exception the export views do not catch (the 500 page).
	ErrMiscExportsServer = errors.New("misc export raised")
	// ErrMiscExportsNotPorted refuses the spreadsheet outputs (the 500 page, before any lookup).
	ErrMiscExportsNotPorted = errors.New("misc export output not ported")
)

// MiscExportPerson is the Associate columns the subscription export renders
// (the subscription's associate or a tutor).
type MiscExportPerson struct {
	FirstName   *string    `db:"first_name"`
	LastName    *string    `db:"last_name"`
	Sex         *string    `db:"sex"`
	TaxCode     *string    `db:"tax_code"`
	BornDate    *time.Time `db:"born_date"`
	BornCity    *string    `db:"born_city"`
	AddressCity *string    `db:"address_city"`
	Address     *string    `db:"address"`
	AddressCap  *string    `db:"address_cap"`
	Email       *string    `db:"email"`
	PhoneLabel  *string    `db:"phone_label"`
	Phone       *string    `db:"phone"`
	Phone2Label *string    `db:"phone_2_label"`
	Phone2      *string    `db:"phone_2"`
	Phone3Label *string    `db:"phone_3_label"`
	Phone3      *string    `db:"phone_3"`
	Phone4Label *string    `db:"phone_4_label"`
	Phone4      *string    `db:"phone_4"`
	IsMinor     bool       `db:"is_minor"`
}

// MiscExportSubscription is one exported subscription with its
// select_related associate and medical certificate.
type MiscExportSubscription struct {
	ID                   string     `db:"subscription_id"`
	Status               int64      `db:"status_flag"`
	Created              time.Time  `db:"creation_date"`
	Start                *time.Time `db:"start_date"`
	End                  *time.Time `db:"end_date"`
	Number               *string    `db:"subscription_number"`
	Kind                 *string    `db:"subscription_type"`
	SignatureKey         *string    `db:"signature_storage_key"`
	SignatureURL         *string    `db:"signature_url"`
	HasMedical           bool       `db:"has_medical"`
	MedicalExpiration    *time.Time `db:"medical_expiration"`
	AssociateID          *string    `db:"associate_id"`
	AssociateAssociation *string    `db:"associate_association"`
	MiscExportPerson
}

// MiscExportRelation is one AssociateTutorRelation with its tutor.
type MiscExportRelation struct {
	ID          int64  `db:"id"`
	AssociateID string `db:"relation_associate_id"`
	Primary     bool   `db:"is_primary"`
	MiscExportPerson
}

// MiscExportInvoice is one exported invoice.
type MiscExportInvoice struct {
	ID         string    `db:"invoice_id"`
	Created    time.Time `db:"creation_date"`
	Membership string    `db:"membership_fee"`
	Activity   string    `db:"activity_fee"`
	Number     *int64    `db:"number"`
	DocumentID *string   `db:"document_pdf_id"`
}

// MiscExportPayment is an invoice's payment_set.first() (primary key order).
type MiscExportPayment struct {
	InvoiceID      string  `db:"invoice_id"`
	Type           string  `db:"type"`
	HasAssociate   bool    `db:"has_associate"`
	AssociateFirst *string `db:"first_name"`
	AssociateLast  *string `db:"last_name"`
}

// MiscExportPromotion is get_main_tutor's audited save of a relation.
type MiscExportPromotion struct {
	RelationID    int64
	Context       LoginAuditContext
	AssociationID *string
	Path          *string
	At            time.Time
}

type MiscExportStore interface {
	BeginMiscExports(ctx context.Context) (MiscExportTransaction, error)
}

type MiscExportTransaction interface {
	Commit() error
	Rollback() error
	// ScopedAssociation is scoped_queryset(SportAssociation.objects).get(user=...) (nil when missing).
	ScopedAssociation(ctx context.Context, user string, scope *string) (*PaymentExportAssociation, error)
	// Subscriptions are the association's group-aware unarchived subscriptions by -creation_date.
	Subscriptions(ctx context.Context, association string, group, scope *string) ([]MiscExportSubscription, error)
	// Tags are the prefetched tag names of each subscription, in the prefetch query's order.
	Tags(ctx context.Context, subscriptions []string) (map[string][]string, error)
	// Relations are the tutor relations of the associates, by primary key.
	Relations(ctx context.Context, associates []string) ([]MiscExportRelation, error)
	PromoteRelation(ctx context.Context, promotion MiscExportPromotion) error
	ActiveAssociationOf(ctx context.Context, user string) (*string, error)
	// Invoices are the association's live group-aware invoices with archived, by creation_date.
	Invoices(ctx context.Context, association string, archived bool, group, scope *string) ([]MiscExportInvoice, error)
	// FirstPayments maps each invoice to its first live group-aware payment by primary key.
	FirstPayments(ctx context.Context, invoices []string, group, scope *string) (map[string]MiscExportPayment, error)
}
