package repository

import (
	"context"

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
