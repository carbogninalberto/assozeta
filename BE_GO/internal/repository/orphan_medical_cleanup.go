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
// the same column list and no ORDER BY. MedicalCertificate.Meta declares no
// ordering, so the row order is PostgreSQL's plan order, as for Django's
// cursor. psycopg2 converts every row of a fetched chunk before Django yields
// any of them: a timestamp outside Python's years (1..9999) raises while the
// chunk is fetched, so nothing in that chunk or a later one is processed.
// Go fetches the same chunks and returns the complete chunks before the one
// that holds such a timestamp, with Truncated set.
func (t *orphanMedicalTransaction) OrphanMedicalCertificates(ctx context.Context) (domain.OrphanMedicalBatch, error) {
	if _, err := t.tx.ExecContext(ctx, `DECLARE "tasks_db_f_orphan_medical" NO SCROLL CURSOR FOR SELECT
 "application_medicalcertificate"."medical_id", "application_medicalcertificate"."document_id", "application_medicalcertificate"."user_id",
 CASE WHEN `+orphanMedicalPythonYear(`"application_medicalcertificate"."creation_date"`)+` THEN "application_medicalcertificate"."creation_date" END,
 "application_medicalcertificate"."expiration_date",
 "application_medicalcertificate"."competitive_medical_certificate", "application_medicalcertificate"."notes",
 "docmanager_document"."document_id",
 CASE WHEN `+orphanMedicalPythonYear(`"docmanager_document"."creation_date"`)+` THEN "docmanager_document"."creation_date" END,
 "docmanager_document"."filepath", "docmanager_document"."filename", "docmanager_document"."file_size_bytes", "docmanager_document"."token",
 NOT COALESCE(`+orphanMedicalPythonYear(`"application_medicalcertificate"."creation_date"`)+`, true)
  OR NOT COALESCE(`+orphanMedicalPythonYear(`"docmanager_document"."creation_date"`)+`, true)
 FROM "application_medicalcertificate" LEFT OUTER JOIN "docmanager_document"
 ON ("application_medicalcertificate"."document_id" = "docmanager_document"."document_id")
 WHERE NOT ("application_medicalcertificate"."medical_id" IN (SELECT U0."medical_id" AS "medical"
 FROM "application_subscription" U0 WHERE U0."medical_id" IS NOT NULL))`); err != nil {
		return domain.OrphanMedicalBatch{}, err
	}
	batch := domain.OrphanMedicalBatch{Certificates: []domain.OrphanMedicalCertificate{}}
	for {
		rows, err := t.tx.QueryxContext(ctx, `FETCH FORWARD 50 FROM "tasks_db_f_orphan_medical"`)
		if err != nil {
			return domain.OrphanMedicalBatch{}, err
		}
		chunk := []domain.OrphanMedicalCertificate{}
		fetched, truncated := 0, false
		for rows.Next() {
			values, err := rows.SliceScan()
			if err != nil {
				_ = rows.Close()
				return domain.OrphanMedicalBatch{}, err
			}
			fetched++
			// psycopg2 builds a Python datetime for every timestamp of the
			// chunk, including columns the task does not use: the query flags a
			// timestamp whose UTC year is outside 1..9999 (and does not return
			// it: the driver's text format cannot carry such a year either).
			if outside, _ := values[13].(bool); outside {
				truncated = true
				break
			}
			item := domain.OrphanMedicalCertificate{MedicalID: tasksDBFText(values[0])}
			if values[7] != nil {
				document, filename := tasksDBFText(values[7]), tasksDBFText(values[10])
				created, ok := values[8].(time.Time)
				if !ok {
					_ = rows.Close()
					return domain.OrphanMedicalBatch{}, errors.New("orphan medical: unexpected document creation date")
				}
				item.DocumentID, item.Created, item.Filename = &document, &created, &filename
			}
			chunk = append(chunk, item)
		}
		if err = rows.Close(); err != nil {
			return domain.OrphanMedicalBatch{}, err
		}
		if err = rows.Err(); err != nil {
			return domain.OrphanMedicalBatch{}, err
		}
		if truncated {
			batch.Truncated = true
			break
		}
		batch.Certificates = append(batch.Certificates, chunk...)
		if fetched < 50 {
			break
		}
	}
	_, err := t.tx.ExecContext(ctx, `CLOSE "tasks_db_f_orphan_medical"`)
	return batch, err
}

// orphanMedicalPythonYear is the SQL test that psycopg2 can build a Python
// datetime from a stored timestamp: its year, in the UTC session Django opens,
// is 1..9999 (NULL for a NULL timestamp).
func orphanMedicalPythonYear(column string) string {
	return `(date_part('year', ` + column + ` AT TIME ZONE 'UTC') BETWEEN 1 AND 9999)`
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

// The collector deletes the document and the rows that cascade from it (the
// certificate among them), clears the SET_NULL references with an update and
// audits each deleted row without index (no actor, no association field). The
// certificate's delete diff is rendered before the collector runs: Django's
// second medical_certificate.delete() is a new Collector on the in-memory
// instance, which logs the same values again.
func (t *orphanMedicalTransaction) DeleteOrphanMedicalDocument(ctx context.Context, medical, document string, at time.Time) (map[string][2]string, error) {
	changes, _, err := storedModelChanges(ctx, t.tx, "application_medicalcertificate", "medical_id", medical, false, false)
	if err != nil {
		return nil, err
	}
	if err = deleteDocumentCascade(ctx, t.tx, document, domain.LoginAuditContext{}, at); err != nil {
		return nil, err
	}
	return changes, nil
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
