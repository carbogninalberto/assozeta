package repository

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

type OrphanMedicalCleanups struct{ db *sqlx.DB }

func NewOrphanMedicalCleanups(db *sqlx.DB) *OrphanMedicalCleanups {
	return &OrphanMedicalCleanups{db: db}
}

func (r *OrphanMedicalCleanups) BeginOrphanMedical(ctx context.Context, job domain.Job) (domain.OrphanMedicalTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	if err = fenceJobEffects(ctx, tx.tx, job.ID, job.LeaseToken); err != nil {
		_ = tx.Rollback()
		return nil, err
	}
	return &orphanMedicalTransaction{ownedTransaction: tx}, nil
}

type orphanMedicalTransaction struct{ *ownedTransaction }

// The queryset Django's .iterator(chunk_size=50) declares as a server-side
// cursor (select_related on the nullable document is a LEFT OUTER JOIN), with
// the same column list and no ORDER BY. A cursor is planned for a fast start
// (cursor_tuple_fraction), so the row order follows that plan, not the plain
// SELECT's: Go declares the same cursor and fetches it in chunks of 50.
func (t *orphanMedicalTransaction) OrphanMedicalCertificates(ctx context.Context) ([]domain.OrphanMedicalCertificate, error) {
	if _, err := t.tx.ExecContext(ctx, `DECLARE "tasks_db_f_orphan_medical" NO SCROLL CURSOR FOR SELECT
 "application_medicalcertificate"."medical_id", "application_medicalcertificate"."document_id", "application_medicalcertificate"."user_id",
 "application_medicalcertificate"."creation_date", "application_medicalcertificate"."expiration_date",
 "application_medicalcertificate"."competitive_medical_certificate", "application_medicalcertificate"."notes",
 "docmanager_document"."document_id", "docmanager_document"."creation_date", "docmanager_document"."filepath",
 "docmanager_document"."filename", "docmanager_document"."file_size_bytes", "docmanager_document"."token"
 FROM "application_medicalcertificate" LEFT OUTER JOIN "docmanager_document"
 ON ("application_medicalcertificate"."document_id" = "docmanager_document"."document_id")
 WHERE NOT ("application_medicalcertificate"."medical_id" IN (SELECT U0."medical_id" AS "medical"
 FROM "application_subscription" U0 WHERE U0."medical_id" IS NOT NULL))`); err != nil {
		return nil, err
	}
	out := []domain.OrphanMedicalCertificate{}
	for {
		rows, err := t.tx.QueryxContext(ctx, `FETCH FORWARD 50 FROM "tasks_db_f_orphan_medical"`)
		if err != nil {
			return nil, err
		}
		fetched := 0
		for rows.Next() {
			values, err := rows.SliceScan()
			if err != nil {
				_ = rows.Close()
				return nil, err
			}
			fetched++
			item := domain.OrphanMedicalCertificate{MedicalID: tasksDBFText(values[0])}
			if values[7] != nil {
				document, filename := tasksDBFText(values[7]), tasksDBFText(values[10])
				created, ok := values[8].(time.Time)
				if !ok {
					_ = rows.Close()
					return nil, errors.New("orphan medical: unexpected document creation date")
				}
				item.DocumentID, item.Created, item.Filename = &document, &created, &filename
			}
			out = append(out, item)
		}
		if err = rows.Close(); err != nil {
			return nil, err
		}
		if err = rows.Err(); err != nil {
			return nil, err
		}
		if fetched < 50 {
			break
		}
	}
	_, err := t.tx.ExecContext(ctx, `CLOSE "tasks_db_f_orphan_medical"`)
	return out, err
}

// tasksDBFText renders a scanned uuid or text column.
func tasksDBFText(value any) string {
	switch v := value.(type) {
	case string:
		return v
	case []byte:
		return string(v)
	case [16]byte:
		return uuid.UUID(v).String()
	case fmt.Stringer:
		return v.String()
	}
	return fmt.Sprint(value)
}

func (t *orphanMedicalTransaction) OrphanMedicalReferenceConflict(ctx context.Context, documents []string) (bool, error) {
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
		// The certificate being deleted is the one reference the task expects.
		allowed := 0
		if ref.Table == "application_medicalcertificate" && ref.Column == "document_id" {
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

// The collector deletes the cascaded certificate, clears the SET_NULL
// references with an update and deletes the document; both deletes are
// audited without index (no association field, no actor).
func (t *orphanMedicalTransaction) DeleteOrphanMedicalDocument(ctx context.Context, medical, document string, at time.Time) (map[string][2]string, error) {
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_medicalcertificate", "medical_id", medical)
	if err != nil {
		return nil, err
	}
	if err = appendModelAudit(ctx, t.tx, orphanMedicalEntry(medical, changes, at)); err != nil {
		return nil, err
	}
	for column := range documentSetNull {
		table, field := splitExportCleanupColumn(column)
		if _, err := t.tx.ExecContext(ctx, `UPDATE `+table+` SET `+field+`=NULL WHERE `+field+`=$1::uuid`, document); err != nil {
			return nil, err
		}
	}
	reverse := map[string]string{"customer_invoice_pdf": "application.CustomerInvoice.None"}
	documentChanges, _, err := deleteModelWithAuditReverse(ctx, t.tx, "docmanager_document", "document_id", document, reverse)
	if err != nil {
		return nil, err
	}
	return changes, appendModelAuditForApp(ctx, t.tx, "docmanager", modelAuditEntry{Model: "document", Key: document,
		Representation: "Document object (" + document + ")", Action: 2, Changes: documentChanges, At: at})
}

func (t *orphanMedicalTransaction) AuditOrphanMedicalDelete(ctx context.Context, medical string, changes map[string][2]string, at time.Time) error {
	return appendModelAudit(ctx, t.tx, orphanMedicalEntry(medical, changes, at))
}

func orphanMedicalEntry(medical string, changes map[string][2]string, at time.Time) modelAuditEntry {
	return modelAuditEntry{Model: "medicalcertificate", Key: medical, Representation: "MedicalCertificate object (" + medical + ")",
		Action: 2, Changes: changes, At: at}
}

func (t *orphanMedicalTransaction) CompleteJob(ctx context.Context, id, token uuid.UUID) error {
	return completeJob(ctx, t.tx, id, token, nil)
}
func (t *orphanMedicalTransaction) Commit() error   { return t.ownedTransaction.Commit() }
func (t *orphanMedicalTransaction) Rollback() error { return t.ownedTransaction.Rollback() }
