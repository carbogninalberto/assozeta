package domain

import (
	"context"
	"encoding/json"
	"time"

	"github.com/google/uuid"
)

// JobCleanupOldExports is the Celery name of cleanup_old_exports_task
// (@shared_task(name="cleanup_old_exports")), as persisted Beat rows name it.
const JobCleanupOldExports = "cleanup_old_exports"

// ExportArchive is one SportAssociationDocumentsArchive row whose document
// filename starts with "export_", with the document fields the task reads.
type ExportArchive struct {
	ArchiveID  string
	DocumentID string
	Created    time.Time
	Filepath   *string
}

type ExportCleanupStore interface {
	// BeginExportCleanup opens a READ COMMITTED transaction fenced by the job
	// lease. Each Django autocommit step is one such transaction.
	BeginExportCleanup(context.Context, Job) (ExportCleanupTransaction, error)
}

type ExportCleanupTransaction interface {
	// ExportAssociations is SportAssociation.original_objects filtered by the
	// associations owning an export archive row, unordered as in Django.
	ExportAssociations(context.Context) ([]string, error)
	// AssociationExports lists the association's export archive rows ordered
	// by -date, skipping the first offset rows (the [max_count:] slice).
	AssociationExports(ctx context.Context, association string, offset int) ([]ExportArchive, error)
	// DeleteExportArchive is export.delete(): the row and its audit entry.
	DeleteExportArchive(ctx context.Context, archive, association string, at time.Time) error
	// DeleteExportDocument is document.delete(): Django's Collector over the
	// document (CASCADE rows and their cascades, SET_NULL updates and audit
	// entries), with no actor.
	DeleteExportDocument(ctx context.Context, document string, at time.Time) error
	CompleteJob(ctx context.Context, id, token uuid.UUID, result json.RawMessage) error
	Commit() error
	Rollback() error
}

// ExportObjects is the default storage delete (S3Storage.delete).
type ExportObjects interface {
	Location() string
	Delete(ctx context.Context, key string) error
}
