package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type DocumentArchive struct {
	db *sqlx.DB
	*Folders
}

func NewDocumentArchive(db *sqlx.DB) *DocumentArchive {
	return &DocumentArchive{db: db, Folders: NewFolders(db)}
}

var _ domain.DocumentArchiveStore = (*DocumentArchive)(nil)

// ArchivedDocuments mirrors the unordered queryset slice: no ORDER BY, like Django.
func (r *DocumentArchive) ArchivedDocuments(ctx context.Context, association string, folder *int64, offset, limit int64) ([]domain.ArchivedDocument, int64, error) {
	where := ` WHERE a.sport_association_id=$1 AND ($2::int IS NULL AND a.folder_id IS NULL OR a.folder_id=$2::int)`
	var total int64
	if err := r.db.GetContext(ctx, &total, `SELECT count(*) FROM application_sportassociationdocumentsarchive a`+where, association, folder); err != nil {
		return nil, 0, err
	}
	rows := []domain.ArchivedDocument{}
	err := r.db.SelectContext(ctx, &rows, `SELECT a.sport_association_documents_archive_id, a.sport_association_id, a.date, a.folder_id,
 d.document_id, d.creation_date, d.filepath, d.filename, d.file_size_bytes, d.token
 FROM application_sportassociationdocumentsarchive a JOIN docmanager_document d ON d.document_id=a.document_id`+where+
		` LIMIT $3 OFFSET $4`, association, folder, limit, offset)
	return rows, total, err
}

var _ domain.ArchiveWriteStore = (*DocumentArchive)(nil)

func (r *DocumentArchive) BeginArchiveWrite(ctx context.Context) (domain.ArchiveWriteTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &archiveWriteTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

type archiveWriteTransaction struct{ *paymentCategoryTransaction }

func (t *archiveWriteTransaction) ArchiveFolder(ctx context.Context, id, association string) (*int64, error) {
	var folder *int64
	err := t.tx.GetContext(ctx, &folder, `SELECT folder_id FROM application_sportassociationdocumentsarchive
 WHERE sport_association_documents_archive_id=$1 AND sport_association_id=$2 FOR UPDATE`, id, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, domain.ErrArchiveNotFound
	}
	return folder, err
}

func (t *archiveWriteTransaction) FolderAssociation(ctx context.Context, id int64) (string, error) {
	var association string
	err := t.tx.GetContext(ctx, &association, `SELECT sport_association_id FROM application_folder WHERE id=$1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return "", domain.ErrFolderTargetMissing
	}
	return association, err
}

func (t *archiveWriteTransaction) SetArchiveFolder(ctx context.Context, id string, folder *int64) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_sportassociationdocumentsarchive SET folder_id=$2
 WHERE sport_association_documents_archive_id=$1`, id, folder)
	return err
}

// The live row resolves through the direct resolver.
func (t *archiveWriteTransaction) AppendArchiveAudit(ctx context.Context, event domain.ArchiveMoveAudit) error {
	path := "direct"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "sportassociationdocumentsarchive", Key: event.ArchiveID,
		Representation: "SportAssociationDocumentsArchive object (" + event.ArchiveID + ")", Action: 1, Changes: event.Changes,
		Context: event.Context, AssociationID: &event.AssociationID, ResolutionPath: &path, At: event.At})
}

func (t *archiveWriteTransaction) DeleteArchive(ctx context.Context, id, association string, audit domain.LoginAuditContext, at time.Time) error {
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_sportassociationdocumentsarchive", "sport_association_documents_archive_id", id)
	if err != nil {
		return err
	}
	path := "changes_json.sport_association"
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "sportassociationdocumentsarchive", Key: id,
		Representation: "SportAssociationDocumentsArchive object (" + id + ")", Action: 2, Changes: changes,
		Context: audit, AssociationID: &association, ResolutionPath: &path, At: at})
}
