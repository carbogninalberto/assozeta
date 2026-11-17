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

// ErrOrphanMedicalUnreachable is a document name no stored row can produce
// (creation_date is written only by the app, filename is UTF-8 text without
// NUL): an application-state error (the 500 page).
var ErrOrphanMedicalUnreachable = refusal.AppState("orphan medical cleanup: unreachable state")

// OrphanMedicalCertificate is one MedicalCertificate the task iterates, with
// the select_related document fields it reads. DocumentID is nil for a
// certificate without document.
type OrphanMedicalCertificate struct {
	MedicalID  string
	DocumentID *string
	Created    *time.Time
	Filename   *string
}

// OrphanMedicalBatch is what one iteration of the queryset yields. Truncated
// is true when a later chunk holds a timestamp outside the years a Python
// datetime can hold: psycopg2 raises while fetching that chunk, so the task
// processes the chunks before it and then its exception handler ends the run.
type OrphanMedicalBatch struct {
	Certificates []OrphanMedicalCertificate
	Truncated    bool
}

type OrphanMedicalStore interface {
	// BeginOrphanMedical opens a READ COMMITTED transaction fenced by the job
	// lease. Each Django autocommit step is one such transaction.
	BeginOrphanMedical(context.Context, Job) (OrphanMedicalTransaction, error)
}

type OrphanMedicalTransaction interface {
	// OrphanMedicalCertificates is MedicalCertificate.objects.exclude(
	// medical_id__in=<every Subscription.medical>).select_related('document')
	// through Django's server-side cursor, in chunks of 50 and unordered as in
	// Django.
	OrphanMedicalCertificates(context.Context) (OrphanMedicalBatch, error)
	// DeleteOrphanMedicalDocument is document.delete(): Django's Collector
	// over the document (the cascaded certificate and any other referencing
	// rows, SET_NULL updates and audit entries). It returns the certificate's
	// delete changes for the second, in-memory certificate delete.
	DeleteOrphanMedicalDocument(ctx context.Context, medical, document string, at time.Time) (map[string][2]string, error)
	// AuditOrphanMedicalDelete is medical_certificate.delete() on the already
	// deleted instance: no row left, a second audited delete entry.
	AuditOrphanMedicalDelete(ctx context.Context, medical string, changes map[string][2]string, at time.Time) error
	CompleteJob(ctx context.Context, id, token uuid.UUID) error
	Commit() error
	Rollback() error
}
