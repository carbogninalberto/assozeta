package repository

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

type MedicalCleanupCandidates struct{ db sqlx.QueryerContext }

func NewMedicalCleanupCandidates(db sqlx.QueryerContext) *MedicalCleanupCandidates {
	return &MedicalCleanupCandidates{db: db}
}

// ReadMedicalCleanupCandidates discovers document groups with only unreferenced
// medical certificates. It takes no locks and never authorizes file/row deletion.
// The caller can supply its transaction; there is no hidden transaction or scope.
func (r *MedicalCleanupCandidates) ReadMedicalCleanupCandidates(ctx context.Context, after *uuid.UUID, limit int) ([]domain.MedicalCleanupDocument, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if limit < 1 || limit > domain.MedicalCleanupPageLimit {
		return nil, domain.ErrMedicalCleanupPage
	}
	rows := []domain.MedicalCleanupDocument{}
	if err := sqlx.SelectContext(ctx, r.db, &rows, medicalCleanupCandidatesSQL, after, limit); err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, err
	}
	for i := range rows {
		rows[i].Created = rows[i].Created.UTC()
	}
	return rows, nil
}

// Historical/deleted/group/foreign references still protect a document. The
// materialized normalized reference set is shared by the page; application memory
// is limited to returned rows, while SQL work still depends on reference volume.
// The integration adapter requires coverage of every actual Document FK/M2M.
const medicalCleanupCandidatesSQL = `WITH protected_medicals AS MATERIALIZED (
 SELECT replace(s.medical_id::text,'-','') AS id FROM application_subscription s WHERE s.medical_id IS NOT NULL
 UNION
 SELECT translate(regexp_replace(lower(btrim(d.data#>>'{medical_certificate,medical_id}')),'^urn:uuid:',''),'{}-','')
 FROM application_associateimportdraft d
 WHERE jsonb_typeof(d.data->'medical_certificate')='object'
 AND jsonb_typeof(d.data->'medical_certificate'->'medical_id')='string'
)
SELECT d.document_id AS id,d.creation_date AS created,d.filename,d.filepath,d.file_size_bytes AS size,c.certificates
FROM docmanager_document d CROSS JOIN LATERAL (
 SELECT count(*) AS certificates FROM application_medicalcertificate m WHERE m.document_id=d.document_id
) c
WHERE ($1::uuid IS NULL OR d.document_id>$1) AND c.certificates>0
 AND NOT EXISTS (
  SELECT 1 FROM application_medicalcertificate m WHERE m.document_id=d.document_id
  AND replace(m.medical_id::text,'-','') IN (SELECT id FROM protected_medicals)
 )
 AND NOT EXISTS (SELECT 1 FROM application_courselocation_documents r WHERE r.document_id=d.document_id)
 AND NOT EXISTS (SELECT 1 FROM application_customerinvoice r WHERE r.pdf_id=d.document_id)
 AND NOT EXISTS (SELECT 1 FROM application_instructor_documents r WHERE r.document_id=d.document_id)
 AND NOT EXISTS (SELECT 1 FROM application_instructorhours r WHERE r.document_id=d.document_id)
 AND NOT EXISTS (SELECT 1 FROM application_invoice r WHERE r.document_pdf_id=d.document_id)
 AND NOT EXISTS (SELECT 1 FROM application_invoice_attached_documents r WHERE r.document_id=d.document_id)
 AND NOT EXISTS (SELECT 1 FROM application_invoicesuppliers r WHERE r.document_pdf_id=d.document_id)
 AND NOT EXISTS (SELECT 1 FROM application_medicalappointments r WHERE r.document_id=d.document_id)
 AND NOT EXISTS (SELECT 1 FROM application_moduleresponses_attachments r WHERE r.document_id=d.document_id)
 AND NOT EXISTS (SELECT 1 FROM application_payment_attachments r WHERE r.document_id=d.document_id)
 AND NOT EXISTS (SELECT 1 FROM application_sportassociationdocumentsarchive r WHERE r.document_id=d.document_id)
 AND NOT EXISTS (SELECT 1 FROM application_subscription r WHERE r.document_pdf_id=d.document_id)
 AND NOT EXISTS (SELECT 1 FROM application_subscriptionfile r WHERE r.document_id=d.document_id)
 AND NOT EXISTS (SELECT 1 FROM application_subscriptionmembership_attached_membership_docu749f r WHERE r.document_id=d.document_id)
ORDER BY d.document_id LIMIT $2`
