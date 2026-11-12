package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

type ExportCleanups struct{ db *sqlx.DB }

func NewExportCleanups(db *sqlx.DB) *ExportCleanups { return &ExportCleanups{db: db} }

func (r *ExportCleanups) BeginExportCleanup(ctx context.Context, job domain.Job) (domain.ExportCleanupTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	if err = fenceJobEffects(ctx, tx.tx, job.ID, job.LeaseToken); err != nil {
		_ = tx.Rollback()
		return nil, err
	}
	return &exportCleanupTransaction{ownedTransaction: tx}, nil
}

type exportCleanupTransaction struct{ *ownedTransaction }

// document__filename__startswith='export_' compiles to LIKE 'export\_%'.
const exportCleanupFilter = `"docmanager_document"."filename"::text LIKE 'export\_%'`

// The SQL Django issues for original_objects.filter(sport_association_id__in=
// <distinct values_list>): no ORDER BY, so PostgreSQL decides the order.
func (t *exportCleanupTransaction) ExportAssociations(ctx context.Context) ([]string, error) {
	associations := []string{}
	err := t.tx.SelectContext(ctx, &associations, `SELECT "application_sportassociation"."sport_association_id"::text
 FROM "application_sportassociation" WHERE "application_sportassociation"."sport_association_id" IN (
 SELECT DISTINCT U0."sport_association_id" FROM "application_sportassociationdocumentsarchive" U0
 INNER JOIN "docmanager_document" U1 ON (U0."document_id" = U1."document_id") WHERE U1."filename"::text LIKE 'export\_%')`)
	return associations, err
}

// The select_related('document').order_by('-date') queryset, with OFFSET for
// the [max_count:] slice. Rows with the same date keep PostgreSQL's order for
// this query, as in Django.
func (t *exportCleanupTransaction) AssociationExports(ctx context.Context, association string, offset int) ([]domain.ExportArchive, error) {
	rows := []struct {
		ArchiveID  string    `db:"archive_id"`
		DocumentID string    `db:"document_id"`
		Created    time.Time `db:"creation_date"`
		Filepath   *string   `db:"filepath"`
	}{}
	query := `SELECT "application_sportassociationdocumentsarchive"."sport_association_documents_archive_id"::text AS archive_id,
 "docmanager_document"."document_id"::text AS document_id, "docmanager_document"."creation_date", "docmanager_document"."filepath"
 FROM "application_sportassociationdocumentsarchive" INNER JOIN "docmanager_document"
 ON ("application_sportassociationdocumentsarchive"."document_id" = "docmanager_document"."document_id")
 WHERE ("application_sportassociationdocumentsarchive"."sport_association_id" = $1::uuid AND ` + exportCleanupFilter + `)
 ORDER BY "application_sportassociationdocumentsarchive"."date" DESC`
	var err error
	if offset > 0 {
		err = t.tx.SelectContext(ctx, &rows, query+` OFFSET $2`, association, offset)
	} else {
		err = t.tx.SelectContext(ctx, &rows, query, association)
	}
	if err != nil {
		return nil, err
	}
	exports := make([]domain.ExportArchive, len(rows))
	for i, row := range rows {
		exports[i] = domain.ExportArchive{ArchiveID: row.ArchiveID, DocumentID: row.DocumentID, Created: row.Created, Filepath: row.Filepath}
	}
	return exports, nil
}

func (t *exportCleanupTransaction) ExportReferenceConflict(ctx context.Context, documents []string) (bool, error) {
	if len(documents) == 0 {
		return false, nil
	}
	references := []struct {
		Table  string `db:"table_name"`
		Column string `db:"column_name"`
	}{}
	if err := t.tx.SelectContext(ctx, &references, `SELECT c.conrelid::regclass::text AS table_name, a.attname AS column_name
 FROM pg_constraint c JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=c.conkey[1]
 WHERE c.contype='f' AND c.confrelid='docmanager_document'::regclass AND array_length(c.conkey, 1)=1`); err != nil {
		return false, err
	}
	for _, ref := range references {
		if documentSetNull[ref.Table+"."+ref.Column] {
			continue
		}
		// The archive row being deleted is the one reference the task expects.
		allowed := 0
		if ref.Table == "application_sportassociationdocumentsarchive" && ref.Column == "document_id" {
			allowed = 1
		}
		var conflict bool
		if err := t.tx.GetContext(ctx, &conflict, `SELECT EXISTS(SELECT 1 FROM `+ref.Table+` WHERE `+ref.Column+`=ANY($1::uuid[])
 GROUP BY `+ref.Column+` HAVING count(*)>$2)`, documents, allowed); err != nil {
			return false, err
		}
		if conflict {
			return true, nil
		}
	}
	return false, nil
}

// The archive has no live object to resolve after the delete: the index comes
// from changes_json.sport_association through SportAssociation.objects (not
// deleted associations only).
func (t *exportCleanupTransaction) DeleteExportArchive(ctx context.Context, archive, association string, at time.Time) error {
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_sportassociationdocumentsarchive", "sport_association_documents_archive_id", archive)
	if err != nil {
		return err
	}
	event := modelAuditEntry{Model: "sportassociationdocumentsarchive", Key: archive,
		Representation: "SportAssociationDocumentsArchive object (" + archive + ")", Action: 2, Changes: changes, At: at}
	var live bool
	err = t.tx.GetContext(ctx, &live, `SELECT true FROM application_sportassociation WHERE sport_association_id=$1::uuid AND NOT deleted`, association)
	if err == nil {
		path := "changes_json.sport_association"
		event.AssociationID, event.ResolutionPath = &association, &path
	} else if !errors.Is(err, sql.ErrNoRows) {
		return err
	}
	return appendModelAudit(ctx, t.tx, event)
}

// Django's collector clears the SET_NULL references with an update; the task
// has no actor, so the Document entry resolves no association index.
func (t *exportCleanupTransaction) DeleteExportDocument(ctx context.Context, document string, at time.Time) error {
	for column := range documentSetNull {
		table, field := splitExportCleanupColumn(column)
		if _, err := t.tx.ExecContext(ctx, `UPDATE `+table+` SET `+field+`=NULL WHERE `+field+`=$1::uuid`, document); err != nil {
			return err
		}
	}
	reverse := map[string]string{"customer_invoice_pdf": "application.CustomerInvoice.None"}
	changes, _, err := deleteModelWithAuditReverse(ctx, t.tx, "docmanager_document", "document_id", document, reverse)
	if err != nil {
		return err
	}
	return appendModelAuditForApp(ctx, t.tx, "docmanager", modelAuditEntry{Model: "document", Key: document,
		Representation: "Document object (" + document + ")", Action: 2, Changes: changes, At: at})
}

func splitExportCleanupColumn(column string) (string, string) {
	for i := len(column) - 1; i >= 0; i-- {
		if column[i] == '.' {
			return column[:i], column[i+1:]
		}
	}
	return column, ""
}

func (t *exportCleanupTransaction) CompleteJob(ctx context.Context, id, token uuid.UUID, result json.RawMessage) error {
	return completeJob(ctx, t.tx, id, token, result)
}
func (t *exportCleanupTransaction) Commit() error   { return t.ownedTransaction.Commit() }
func (t *exportCleanupTransaction) Rollback() error { return t.ownedTransaction.Rollback() }
