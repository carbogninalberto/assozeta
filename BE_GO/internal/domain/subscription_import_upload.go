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
	// With effects.Failed only the two status changes are written. Otherwise
	// document.delete() of the upload follows them (audited, indexed to the
	// actor's association).
	ImportRows(ctx context.Context, association, user string, actor *string, effects SubscriptionImportEffects, audit LoginAuditContext,
		at func() time.Time) error
	// RemoveDocuments is Document.delete() of each existing id, in order
	// (audited, indexed to the actor's association).
	RemoveDocuments(ctx context.Context, ids []string, actor *string, audit LoginAuditContext, at func() time.Time) error
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
	// Failed is process_import_rows raising: the rows are rolled back.
	Failed bool
	// DocumentID is the upload a completed import removes.
	DocumentID   string
	StatusID     string
	Certificates []SubscriptionImportCertificate
	Drafts       []SubscriptionImportDraft
}

// SubscriptionImportObjects is default_storage.open(...).read().
type SubscriptionImportObjects interface {
	Read(ctx context.Context, key string) ([]byte, error)
	// Delete is default_storage.delete(name).
	Delete(ctx context.Context, key string) error
	// Keys are the object keys under prefix, in key order.
	Keys(ctx context.Context, prefix string) ([]string, error)
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
