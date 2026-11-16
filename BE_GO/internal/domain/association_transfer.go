package domain

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrAssociationTransferUnexpected is a state only the application writes:
// the export cache snapshots and task owner entries (export_views.py), the
// django-db Celery result rows of its own tasks (JSON, CELERY_RESULT_BACKEND
// "django-db", BE/core/settings.py:176), the export documents' storage paths,
// the generated import storage names and the server composition (the writes
// are always configured, cmd/server/association_transfer.go:29). Values Django
// cannot render (NaN, infinities) are its 500 page as well.
var ErrAssociationTransferUnexpected = refusal.AppState("association transfer: unexpected application state")

// AssociationExportRow is one SportAssociationDocumentsArchive row with its
// Document, as export/list renders it.
type AssociationExportRow struct {
	ArchiveID  string    `db:"archive_id"`
	DocumentID string    `db:"document_id"`
	Filename   string    `db:"filename"`
	Filepath   *string   `db:"filepath"`
	FileSize   *int64    `db:"file_size_bytes"`
	Date       time.Time `db:"date"`
	Created    time.Time `db:"creation_date"`
}

// AssociationTaskResult is a django-celery-results TaskResult row.
type AssociationTaskResult struct {
	Status   string  `db:"status"`
	Result   *string `db:"result"`
	Meta     *string `db:"meta"`
	Encoding string  `db:"content_encoding"`
	// TaskName is null unless the worker stores extended results.
	TaskName *string `db:"task_name"`
}

// AssociationTransferStore reads what the export/import views read.
type AssociationTransferStore interface {
	// OwnedAssociation is User.sport_association for an owner:
	// SportAssociation.objects (not deleted).get(user=user).
	OwnedAssociation(ctx context.Context, user string) (string, bool, error)
	ExportCount(ctx context.Context, association string) (int, error)
	Exports(ctx context.Context, association string, limit int) ([]AssociationExportRow, error)
	ExportArchive(ctx context.Context, association, document string) (bool, error)
	// PrimaryOwner is the first InstanceConfiguration and its primary
	// association's owner (nil when none is set).
	PrimaryOwner(ctx context.Context) (bool, *string, error)
	TaskResult(ctx context.Context, task string) (*AssociationTaskResult, error)
	// CacheKeysExist reports whether any of the Django cache keys exists.
	CacheKeysExist(ctx context.Context, keys ...string) (bool, error)
}
