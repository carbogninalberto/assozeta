package domain

import (
	"context"
	"time"
)

type SubscriptionImportStore interface {
	BeginSubscriptionImport(context.Context) (SubscriptionImportTransaction, error)
	// ImportRows is the mapping branch's writes, in the view's order:
	// manage_import_status('start') (the association's IMPORTING statuses
	// deleted, one created), each certificate (created for user, then its
	// expiration saved or the row deleted), bulk_create of the drafts (no
	// audit entries) and manage_import_status('end'). at gives each write's
	// timezone.now().
	ImportRows(ctx context.Context, association, user string, actor *string, effects SubscriptionImportEffects, audit LoginAuditContext,
		at func() time.Time) error
}

// SubscriptionImportDocument is the Document whose stored file is imported.
type SubscriptionImportDocument struct {
	Created  time.Time
	Filename string
}

// SubscriptionImportCertificate is one MedicalCertificate of the import; a
// nil Expiration is the certificate deleted because its text was no date.
type SubscriptionImportCertificate struct {
	ID         string
	Expiration *time.Time
}

// SubscriptionImportDraft is one AssociateImportDraft: JSON is the stored
// data (Data is the value before it is rendered).
type SubscriptionImportDraft struct {
	ID    string
	Data  any
	JSON  []byte
	Valid bool
}

type SubscriptionImportEffects struct {
	StatusID     string
	Certificates []SubscriptionImportCertificate
	Drafts       []SubscriptionImportDraft
}

// SubscriptionImportObjects is default_storage.open(...).read().
type SubscriptionImportObjects interface {
	Read(ctx context.Context, key string) ([]byte, error)
}

type SubscriptionImportTransaction interface {
	// Association is scoped_queryset(request, SportAssociation.objects).filter(user=...).first().
	Association(ctx context.Context, user string, impersonation *string) (*string, error)
	// DocumentExists is Document.objects.filter(document_id=...).exists().
	DocumentExists(ctx context.Context, id string) (bool, error)
	// Document is Document.objects.filter(document_id=...).first().
	Document(ctx context.Context, id string) (*SubscriptionImportDocument, error)
	Rollback() error
}
