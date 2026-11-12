package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// ConsumerRestoreExportStore is the database side of export_association_data
// in the Celery consumer (BE/application/tasks.py and export_service.py).
// Every write is its own autocommit unit with its auditlog entry, as in the
// Django task (no request: no actor, cid or remote address).
type ConsumerRestoreExportStore struct {
	db  *sqlx.DB
	zip *ConsumerExportZipStore
}

func NewConsumerRestoreExportStore(db *sqlx.DB) *ConsumerRestoreExportStore {
	return &ConsumerRestoreExportStore{db: db, zip: NewConsumerExportZipStore(db)}
}

// ExportUser is User.objects.get(user_id=...) (the default manager excludes
// deleted users).
type ExportUser struct {
	Found            bool
	Email, FirstName string
}

func (r *ConsumerRestoreExportStore) ExportUser(ctx context.Context, id string) (ExportUser, error) {
	var row struct {
		Email     string `db:"email"`
		FirstName string `db:"first_name"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT email, first_name FROM bakney_user WHERE NOT deleted AND user_id=$1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return ExportUser{}, nil
	}
	if err != nil {
		return ExportUser{}, err
	}
	return ExportUser{Found: true, Email: row.Email, FirstName: row.FirstName}, nil
}

// ExportAssociation is SportAssociation.original_objects.get(...) (deleted
// rows included).
type ExportAssociation struct {
	Found        bool
	Owner        string
	Denomination string
	TaxCode      *string
}

func (r *ConsumerRestoreExportStore) ExportAssociation(ctx context.Context, id string) (ExportAssociation, error) {
	var row struct {
		Owner        string  `db:"user_id"`
		Denomination string  `db:"denomination"`
		TaxCode      *string `db:"tax_code"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT user_id::text AS user_id, denomination, tax_code FROM application_sportassociation
 WHERE sport_association_id=$1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return ExportAssociation{}, nil
	}
	if err != nil {
		return ExportAssociation{}, err
	}
	return ExportAssociation{Found: true, Owner: row.Owner, Denomination: row.Denomination, TaxCode: row.TaxCode}, nil
}

// DocumentReferences counts the rows of every foreign key to
// docmanager_document other than the documents archive that point at id:
// Document.delete()'s collector would cascade, protect or clear them.
func (r *ConsumerRestoreExportStore) DocumentReferences(ctx context.Context, id string) (int64, error) {
	var references []struct {
		Table  string `db:"table_name"`
		Column string `db:"column_name"`
	}
	if err := r.db.SelectContext(ctx, &references, `SELECT c.conrelid::regclass::text AS table_name, a.attname AS column_name
 FROM pg_constraint c JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=c.conkey[1]
 WHERE c.contype='f' AND c.confrelid='docmanager_document'::regclass AND array_length(c.conkey, 1)=1
 ORDER BY 1, 2`); err != nil {
		return 0, err
	}
	var total int64
	for _, reference := range references {
		if reference.Table == "application_sportassociationdocumentsarchive" && reference.Column == "document_id" {
			continue
		}
		var count int64
		if err := r.db.GetContext(ctx, &count, `SELECT count(*) FROM `+consumerRestoreQuotePath(reference.Table)+` WHERE `+
			consumerRestoreQuote(reference.Column)+`=$1`, id); err != nil {
			return 0, err
		}
		total += count
	}
	return total, nil
}

func consumerRestoreQuote(name string) string { return strconv.Quote(name) }

// quoteIdentifierPath quotes a regclass text that may already be quoted.
func consumerRestoreQuotePath(name string) string {
	if len(name) > 0 && name[0] == '"' {
		return name
	}
	return consumerRestoreQuote(name)
}

// DeleteArchive is export.delete() of cleanup_old_exports: the archive row
// and its delete entry indexed through the deleted row's association.
func (r *ConsumerRestoreExportStore) DeleteArchive(ctx context.Context, id, association string, at time.Time) error {
	tx, err := r.db.BeginTxx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	changes, _, err := deleteModelWithAudit(ctx, tx, "application_sportassociationdocumentsarchive", "sport_association_documents_archive_id", id)
	if err != nil {
		return err
	}
	path := "changes_json.sport_association"
	if err = appendModelAudit(ctx, tx, modelAuditEntry{Model: "sportassociationdocumentsarchive", Key: id,
		Representation: "SportAssociationDocumentsArchive object (" + id + ")", Action: 2, Changes: changes,
		AssociationID: &association, ResolutionPath: &path, At: at}); err != nil {
		return err
	}
	return tx.Commit()
}

// DeleteDocument is document.delete() for a document nothing else references
// (checked by DocumentReferences beforehand): the row and its delete entry.
func (r *ConsumerRestoreExportStore) DeleteDocument(ctx context.Context, id string, at time.Time) error {
	tx, err := r.db.BeginTxx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	changes, _, err := deleteModelWithAuditReverse(ctx, tx, "docmanager_document", "document_id", id, documentReverse)
	if err != nil {
		return err
	}
	if err = appendModelAuditForApp(ctx, tx, "docmanager", documentAudit(id, 2, changes, nil, domain.LoginAuditContext{}, at)); err != nil {
		return err
	}
	return tx.Commit()
}

// CreateDocument is Document.objects.create(filename=...).
func (r *ConsumerRestoreExportStore) CreateDocument(ctx context.Context, document domain.NewDocument, at time.Time) error {
	return r.zip.CreateDocument(ctx, document, at)
}

// SetDocumentFile is document.save(update_fields=['filepath',
// 'file_size_bytes']) after the upload, with its update entry.
func (r *ConsumerRestoreExportStore) SetDocumentFile(ctx context.Context, id, path string, size int64, at time.Time) error {
	tx, err := r.db.BeginTxx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(ctx, `UPDATE docmanager_document SET filepath=$2, file_size_bytes=$3 WHERE document_id=$1`, id, path, size); err != nil {
		return err
	}
	changes := map[string][2]string{"filepath": {"None", path}, "file_size_bytes": {"None", strconv.FormatInt(size, 10)}}
	if err = appendModelAuditForApp(ctx, tx, "docmanager", documentAudit(id, 1, changes, nil, domain.LoginAuditContext{}, at)); err != nil {
		return err
	}
	return tx.Commit()
}

// CreateArchive is SportAssociationDocumentsArchive.objects.create(...).
func (r *ConsumerRestoreExportStore) CreateArchive(ctx context.Context, row domain.ArchiveRecord, created, at time.Time) error {
	return r.zip.CreateArchive(ctx, row, created, at)
}
