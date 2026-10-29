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

func (t *archiveWriteTransaction) ArchiveOwners(ctx context.Context, ids []string) (map[string]string, error) {
	rows := []struct {
		ID          string `db:"sport_association_documents_archive_id"`
		Association string `db:"sport_association_id"`
	}{}
	if err := t.tx.SelectContext(ctx, &rows, `SELECT sport_association_documents_archive_id, sport_association_id
 FROM application_sportassociationdocumentsarchive WHERE sport_association_documents_archive_id=ANY($1::uuid[]) FOR UPDATE`, ids); err != nil {
		return nil, err
	}
	owners := make(map[string]string, len(rows))
	for _, row := range rows {
		owners[row.ID] = row.Association
	}
	return owners, nil
}

func (t *archiveWriteTransaction) ActiveAssociationOf(ctx context.Context, user string) (*string, error) {
	var id string
	err := t.tx.GetContext(ctx, &id, `SELECT sport_association_id FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted ORDER BY sport_association_id LIMIT 1`, user)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &id, err
}

// documentSetNull are the Document foreign keys declared on_delete=SET_NULL;
// Django's collector clears them with an update, without model saves.
var documentSetNull = map[string]bool{
	"application_subscription.document_pdf_id": true, "application_instructorhours.document_id": true,
	"application_invoice.document_pdf_id": true, "application_invoicesuppliers.document_pdf_id": true,
	"application_customerinvoice.pdf_id": true,
}

// BulkDeleteArchives mirrors archives_to_delete.delete() then
// Document.objects.filter(document_id__in=...).delete() in one transaction.
func (t *archiveWriteTransaction) BulkDeleteArchives(ctx context.Context, ids []string, association string, actorAssociation *string, audit domain.LoginAuditContext, at time.Time) error {
	documents := []string{}
	if err := t.tx.SelectContext(ctx, &documents, `SELECT document_id FROM application_sportassociationdocumentsarchive
 WHERE sport_association_documents_archive_id=ANY($1::uuid[])`, ids); err != nil {
		return err
	}
	for _, id := range ids {
		if err := t.DeleteArchive(ctx, id, association, audit, at); err != nil {
			return err
		}
	}
	references := []struct {
		Table  string `db:"table_name"`
		Column string `db:"column_name"`
	}{}
	if err := t.tx.SelectContext(ctx, &references, `SELECT c.conrelid::regclass::text AS table_name, a.attname AS column_name
 FROM pg_constraint c JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=c.conkey[1]
 WHERE c.contype='f' AND c.confrelid='docmanager_document'::regclass AND array_length(c.conkey, 1)=1`); err != nil {
		return err
	}
	for _, ref := range references {
		if documentSetNull[ref.Table+"."+ref.Column] {
			if _, err := t.tx.ExecContext(ctx, `UPDATE `+ref.Table+` SET `+ref.Column+`=NULL WHERE `+ref.Column+`=ANY($1::uuid[])`, documents); err != nil {
				return err
			}
			continue
		}
		var referenced bool
		if err := t.tx.GetContext(ctx, &referenced, `SELECT EXISTS(SELECT 1 FROM `+ref.Table+` WHERE `+ref.Column+`=ANY($1::uuid[]))`, documents); err != nil {
			return err
		}
		if referenced {
			return domain.ErrDocumentCascadeUnsupported
		}
	}
	reverse := map[string]string{"customer_invoice_pdf": "application.CustomerInvoice.None"}
	for _, id := range documents {
		changes, _, err := deleteModelWithAuditReverse(ctx, t.tx, "docmanager_document", "document_id", id, reverse)
		if err != nil {
			return err
		}
		// Document has no resolver and no association field: the actor's association, if any.
		event := modelAuditEntry{Model: "document", Key: id, Representation: "Document object (" + id + ")", Action: 2,
			Changes: changes, Context: audit, At: at}
		if actorAssociation != nil {
			path := "actor.sport_association"
			event.AssociationID, event.ResolutionPath = actorAssociation, &path
		}
		if err = appendModelAuditForApp(ctx, t.tx, "docmanager", event); err != nil {
			return err
		}
	}
	return nil
}
