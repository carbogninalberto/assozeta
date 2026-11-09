package domain

import (
	"context"
	"errors"
	"time"
)

// ErrAssociationTransferNotPorted refuses association export/import paths
// whose Django behaviour needs storage, Celery or cache values this port does
// not read (see docs/architecture/go-migration/association-transfer.md).
var ErrAssociationTransferNotPorted = errors.New("association transfer path not ported")

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
