package domain

import (
	"context"
	"errors"
	"time"
)

// ArchivedDocument is one SportAssociationDocumentsArchive row with its Document.
type ArchivedDocument struct {
	ID            string    `db:"sport_association_documents_archive_id"`
	AssociationID string    `db:"sport_association_id"`
	Date          time.Time `db:"date"`
	FolderID      *int64    `db:"folder_id"`
	DocumentID    string    `db:"document_id"`
	CreationDate  time.Time `db:"creation_date"`
	Filepath      *string   `db:"filepath"`
	Filename      string    `db:"filename"`
	FileSize      *int64    `db:"file_size_bytes"`
	Token         string    `db:"token"`
}

type DocumentArchiveStore interface {
	ActiveAssociationForUser(ctx context.Context, user string) (string, error)
	FolderByID(ctx context.Context, id int64) (Folder, error)
	// ArchivedDocuments is the unordered page and the total count; folder nil means root.
	ArchivedDocuments(ctx context.Context, association string, folder *int64, offset, limit int64) ([]ArchivedDocument, int64, error)
}

var ErrArchiveNotFound = errors.New("No SportAssociationDocumentsArchive matches the given query.")

type ArchiveMoveAudit struct {
	ArchiveID, AssociationID string
	Changes                  map[string][2]string
	Context                  LoginAuditContext
	At                       time.Time
}

type ArchiveWriteStore interface {
	BeginArchiveWrite(context.Context) (ArchiveWriteTransaction, error)
}

type ArchiveWriteTransaction interface {
	AssociationForOwner(context.Context, string, *string) (string, error)
	// ArchiveFolder locks the association's archive row and returns its folder.
	ArchiveFolder(ctx context.Context, id, association string) (*int64, error)
	FolderAssociation(ctx context.Context, id int64) (string, error)
	SetArchiveFolder(ctx context.Context, id string, folder *int64) error
	AppendArchiveAudit(context.Context, ArchiveMoveAudit) error
	// DeleteArchive deletes the row and appends auditlog's delete entry.
	DeleteArchive(ctx context.Context, id, association string, audit LoginAuditContext, at time.Time) error
	Commit() error
	Rollback() error
}
