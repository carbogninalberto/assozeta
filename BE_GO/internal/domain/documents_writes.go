package domain

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

var (
	// ErrDocumentWritesNotPorted refuses, before any write, what the Go port
	// does not reproduce (see docs/architecture/go-migration/documents-writes.md).
	ErrDocumentWritesNotPorted = refusal.New("document upload behaviour not ported")
	// ErrDocumentWritesFailed is an exception the views do not handle (the 500 page).
	ErrDocumentWritesFailed = errors.New("document upload request failed")
)

// RequestFile is one multipart file as Django's MultiPartParser and
// UploadedFile present it: the sanitized name and the received bytes.
type RequestFile struct {
	Name    string
	Content []byte
}

// NewDocument is a Document row an upload creates (uuid4 id and token,
// timezone.now() creation date); Filename is the stored text.
type NewDocument struct {
	ID, Token, Filename string
	Created             time.Time
}

// ArchiveRecord is a SportAssociationDocumentsArchive row (Date is YYYY-MM-DD).
type ArchiveRecord struct {
	ID, Association, Document, Date string
	Folder                          *int64
}

// UploadSubscription is the Subscription an upload-document request resolved.
type UploadSubscription struct {
	ID, Association string
}

type DocumentUploadTransaction interface {
	SubscriptionMedicalTransaction
	// AssociationForOwner is SportAssociation.objects.get(user=owner) (ErrAssociationNotFound when missing).
	AssociationForOwner(ctx context.Context, owner string, impersonation *string) (string, error)
	// FolderAssociation is Folder.objects.get(id=...)'s association (ErrFolderTargetMissing when missing).
	FolderAssociation(ctx context.Context, id int64) (string, error)
	// FolderExists is the related field's Folder lookup for the update serializer.
	FolderExists(ctx context.Context, id int64) (association string, found bool, err error)
	// AssociationExists is the related field's SportAssociation lookup.
	AssociationExists(ctx context.Context, id string) (bool, error)
	// CurrentAssociation is tenant_scope's SportAssociation.objects.filter(user=user).first().
	CurrentAssociation(ctx context.Context, user string) (*string, error)
	// CreateDocument inserts the Document with its auditlog create entry.
	CreateDocument(ctx context.Context, document NewDocument, actorAssociation *string, audit LoginAuditContext, at time.Time) error
	// DeleteDocument is document.delete(): the collector deletes the archive
	// rows referencing it (audited), then the Document (audited).
	DeleteDocument(ctx context.Context, document NewDocument, association string, actorAssociation *string, audit LoginAuditContext,
		at time.Time) error
	// CreateArchive inserts the archive row; created is its timezone.now() date default.
	CreateArchive(ctx context.Context, row ArchiveRecord, created time.Time, audit LoginAuditContext, at time.Time) error
	// Archive locks the association's archive row (ErrArchiveNotFound when missing).
	Archive(ctx context.Context, id, association string) (ArchiveRecord, error)
	// ArchiveIDTaken is the UniqueValidator lookup (other rows only).
	ArchiveIDTaken(ctx context.Context, id, except string) (bool, error)
	// SaveArchive is Model.save() of the updated row (changes: auditlog's update diff).
	SaveArchive(ctx context.Context, row ArchiveRecord, changes map[string][2]string, audit LoginAuditContext, at time.Time) error
	// InsertArchive is Model.save() under a new primary key: an INSERT, logged
	// as an update against None (before the row exists) and a create.
	InsertArchive(ctx context.Context, row ArchiveRecord, audit LoginAuditContext, at time.Time) error
	// UploadSubscription is scoped_queryset(request, Subscription.objects).filter(subscription_id=id).first().
	UploadSubscription(ctx context.Context, id string, impersonation, group *string) (*UploadSubscription, error)
	// CreateSubscriptionFile inserts the SubscriptionFile with its create entry.
	CreateSubscriptionFile(ctx context.Context, id string, subscription UploadSubscription, document string, created time.Time,
		audit LoginAuditContext, at time.Time) error
	// CreateUploadedCertificate is MedicalCertificate.objects.create(document=..., user=...) with its create entry.
	CreateUploadedCertificate(ctx context.Context, id, document string, user *string, created time.Time, actorAssociation *string,
		audit LoginAuditContext, at time.Time) error
	// AttachCertificate is Subscription.save() after medical_id = certificate:
	// the medical change and the dates Subscription.save() filled.
	AttachCertificate(ctx context.Context, subscription MedicalSubscription, certificate string, dates SubscriptionDates,
		audit LoginAuditContext, at time.Time) error
}

type DocumentUploadStore interface {
	BeginDocumentUpload(context.Context) (DocumentUploadTransaction, error)
}

// DocumentObjectStore is default_storage for uploads: exists (HeadObject) and
// _save (one PutObject under the AWS_LOCATION key).
type DocumentObjectStore interface {
	Location() string
	Exists(ctx context.Context, key string) (bool, error)
	Put(ctx context.Context, key string, body []byte, contentType, contentEncoding string) error
}

// MedicalUploadGrants is remember_medical_upload's cache write.
type MedicalUploadGrants interface {
	RememberMedicalUpload(ctx context.Context, session, certificate, association string) error
}
