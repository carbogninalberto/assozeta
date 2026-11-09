package repository

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// ConsumerExportZipStore is the database side of export_invoices_to_zip in
// the Celery consumer. Each write is its own autocommit unit with its
// auditlog entry, as in the Django task (no request, so no actor, cid or
// remote address).
type ConsumerExportZipStore struct{ db *sqlx.DB }

func NewConsumerExportZipStore(db *sqlx.DB) *ConsumerExportZipStore {
	return &ConsumerExportZipStore{db: db}
}

// ConsumerExportDocument is one source row of the archive.
type ConsumerExportDocument struct {
	ID       uuid.UUID `db:"document_id"`
	Created  time.Time `db:"creation_date"`
	Filepath *string   `db:"filepath"`
	Filename string    `db:"filename"`
}

// CreateDocument is Document.objects.create(filename=...) and its create
// entry. Document has no audit resolver and the task has no actor: the entry
// is not indexed.
func (r *ConsumerExportZipStore) CreateDocument(ctx context.Context, document domain.NewDocument, at time.Time) error {
	tx, err := r.db.BeginTxx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(ctx, `INSERT INTO docmanager_document (document_id, creation_date, filepath, filename, file_size_bytes, token)
 VALUES ($1, $2, NULL, $3, NULL, $4)`, document.ID, document.Created, document.Filename, document.Token); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, tx, "docmanager_document", "document_id", document.ID)
	if err != nil {
		return err
	}
	for name, rendered := range documentReverse {
		changes[name] = [2]string{"None", rendered}
	}
	if err = appendModelAuditForApp(ctx, tx, "docmanager", documentAudit(document.ID, 0, changes, nil, domain.LoginAuditContext{}, at)); err != nil {
		return err
	}
	return tx.Commit()
}

// ActiveAssociation is SportAssociation.objects.filter(sport_association_id=id).first()
// (the ActiveManager excludes deleted rows).
func (r *ConsumerExportZipStore) ActiveAssociation(ctx context.Context, id uuid.UUID) (bool, error) {
	var found bool
	err := r.db.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_sportassociation
 WHERE NOT deleted AND sport_association_id=$1)`, id)
	return found, err
}

// CreateArchive is SportAssociationDocumentsArchive.objects.create(...) with
// its create entry, indexed directly on the association.
func (r *ConsumerExportZipStore) CreateArchive(ctx context.Context, row domain.ArchiveRecord, created, at time.Time) error {
	tx, err := r.db.BeginTxx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(ctx, `INSERT INTO application_sportassociationdocumentsarchive
 (sport_association_documents_archive_id, sport_association_id, document_id, date, folder_id) VALUES ($1, $2, $3, $4::date, NULL)`,
		row.ID, row.Association, row.Document, created.UTC().Format("2006-01-02")); err != nil {
		return err
	}
	changes := map[string][2]string{"sport_association_documents_archive_id": {"None", row.ID}, "sport_association": {"None", row.Association},
		"document": {"None", row.Document}, "date": {"None", pythonAwareUTC(created)}, "folder": {"None", "None"}}
	if err = appendModelAudit(ctx, tx, archiveAudit(row.ID, 0, changes, row.Association, "direct", domain.LoginAuditContext{}, at)); err != nil {
		return err
	}
	return tx.Commit()
}

// Documents is Document.objects.filter(document_id__in=ids), evaluated
// without an ORDER BY as Django does (Document has no Meta.ordering).
func (r *ConsumerExportZipStore) Documents(ctx context.Context, ids []uuid.UUID) ([]ConsumerExportDocument, error) {
	rows := []ConsumerExportDocument{}
	if len(ids) == 0 {
		return rows, nil
	}
	text := make([]string, len(ids))
	for i, id := range ids {
		text[i] = id.String()
	}
	err := r.db.SelectContext(ctx, &rows, `SELECT document_id, creation_date, filepath, filename FROM docmanager_document
 WHERE document_id = ANY($1::text[]::uuid[])`, text)
	return rows, err
}
