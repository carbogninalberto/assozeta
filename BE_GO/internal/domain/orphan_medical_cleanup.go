package domain

import (
	"context"
	"time"

	"github.com/google/uuid"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// JobAutoDeleteOrphanMedicalCertificate is the Celery name of
// auto_delete_orphan_medical_certificate, as persisted Beat rows name it.
const JobAutoDeleteOrphanMedicalCertificate = "auto_delete_orphan_medical_certificate"

// ErrOrphanMedicalUnsupported refuses a run before any effect: a document in
// scope is referenced by a row Django's collector would cascade to (other
// than its one certificate), or its storage name is outside the modelled subset.
var ErrOrphanMedicalUnsupported = refusal.New("orphan medical cleanup needs behaviour Go does not reproduce")

// OrphanMedicalCertificate is one MedicalCertificate the task iterates, with
// the select_related document fields it reads. DocumentID is nil for a
// certificate without document.
type OrphanMedicalCertificate struct {
	MedicalID  string
	DocumentID *string
	Created    *time.Time
	Filename   *string
}

type OrphanMedicalStore interface {
	// BeginOrphanMedical opens a READ COMMITTED transaction fenced by the job
	// lease. Each Django autocommit step is one such transaction.
	BeginOrphanMedical(context.Context, Job) (OrphanMedicalTransaction, error)
}

type OrphanMedicalTransaction interface {
	// OrphanMedicalCertificates is MedicalCertificate.objects.exclude(
	// medical_id__in=<every Subscription.medical>).select_related('document'),
	// unordered as in Django.
	OrphanMedicalCertificates(context.Context) ([]OrphanMedicalCertificate, error)
	// OrphanMedicalReferenceConflict reports whether a document is referenced,
	// outside the SET_NULL foreign keys, by anything but exactly one certificate.
	OrphanMedicalReferenceConflict(ctx context.Context, documents []string) (bool, error)
	// DeleteOrphanMedicalDocument is document.delete(): the cascaded
	// certificate rows deleted and audited, the SET_NULL references cleared,
	// the document deleted and audited. It returns the certificate's delete changes.
	DeleteOrphanMedicalDocument(ctx context.Context, medical, document string, at time.Time) (map[string][2]string, error)
	// AuditOrphanMedicalDelete is medical_certificate.delete() on the already
	// deleted instance: no row left, a second audited delete entry.
	AuditOrphanMedicalDelete(ctx context.Context, medical string, changes map[string][2]string, at time.Time) error
	CompleteJob(ctx context.Context, id, token uuid.UUID) error
	Commit() error
	Rollback() error
}
