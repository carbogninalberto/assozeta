package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// DocmanagerReads reads docmanager Documents for retrieve_document and the
// rows that reference them. Managers are reproduced per model: Subscription is
// GroupAwareManager, Invoice is SoftDeleteGroupAwareManager, SportAssociation
// is ActiveManager; MedicalCertificate, archives and Document use the default.
type DocmanagerReads struct {
	MemberDocuments
	db *sqlx.DB
}

func NewDocmanagerReads(db *sqlx.DB) *DocmanagerReads {
	return &DocmanagerReads{MemberDocuments: NewMemberDocuments(db), db: db}
}

var _ domain.DocmanagerReadsStore = (*DocmanagerReads)(nil)

const storedDocumentColumns = `d.document_id::text AS document_id, d.filename, d.filepath, d.creation_date`

func (r *DocmanagerReads) one(ctx context.Context, query string, args ...any) (*domain.StoredDocument, error) {
	var row domain.StoredDocument
	err := r.db.GetContext(ctx, &row, query, args...)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (r *DocmanagerReads) Document(ctx context.Context, id string) (*domain.StoredDocument, error) {
	return r.one(ctx, `SELECT `+storedDocumentColumns+` FROM docmanager_document d WHERE d.document_id=$1::uuid`, id)
}

func (r *DocmanagerReads) DocumentWithToken(ctx context.Context, id, token string) (*domain.StoredDocument, error) {
	return r.one(ctx, `SELECT `+storedDocumentColumns+` FROM docmanager_document d WHERE d.document_id=$1::uuid AND d.token=$2::uuid
 ORDER BY d.document_id LIMIT 1`, id, token)
}

func (r *DocmanagerReads) SubscriptionDocument(ctx context.Context, subscription string, group *string) (*domain.StoredDocument, error) {
	return r.one(ctx, `SELECT `+storedDocumentColumns+` FROM application_subscription s
  JOIN docmanager_document d ON d.document_id=s.document_pdf_id
 WHERE s.subscription_id=$1::uuid AND ($2::uuid IS NULL OR s.group_id=$2::uuid)`, subscription, group)
}

func (r *DocmanagerReads) MedicalDocument(ctx context.Context, medical string) (*domain.StoredDocument, error) {
	return r.one(ctx, `SELECT `+storedDocumentColumns+` FROM application_medicalcertificate m
  JOIN docmanager_document d ON d.document_id=m.document_id WHERE m.medical_id=$1::uuid`, medical)
}

func (r *DocmanagerReads) InvoiceDocument(ctx context.Context, invoice string, group *string) (*domain.StoredDocument, error) {
	return r.one(ctx, `SELECT `+storedDocumentColumns+` FROM application_invoice i
  JOIN docmanager_document d ON d.document_id=i.document_pdf_id
 WHERE i.invoice_id=$1::uuid AND NOT i.deleted AND ($2::uuid IS NULL OR i.group_id=$2::uuid)`, invoice, group)
}

// ExportArchiveDocument is the download-token archive filter: the token's
// association, this document, a filename starting with 'export_'; .first().
func (r *DocmanagerReads) ExportArchiveDocument(ctx context.Context, association, document string) (*domain.StoredDocument, error) {
	return r.one(ctx, `SELECT `+storedDocumentColumns+` FROM application_sportassociationdocumentsarchive a
  JOIN docmanager_document d ON d.document_id=a.document_id
  JOIN application_sportassociation sa ON sa.sport_association_id=a.sport_association_id
 WHERE a.sport_association_id=$1::uuid AND a.document_id=$2::uuid AND d.filename LIKE 'export\_%'
 ORDER BY a.sport_association_documents_archive_id LIMIT 1`, association, document)
}

// FirstArchiveOwner is the owning user of the document's first archive row.
func (r *DocmanagerReads) FirstArchiveOwner(ctx context.Context, document string) (string, bool, error) {
	var owner string
	err := r.db.GetContext(ctx, &owner, `SELECT sa.user_id::text FROM application_sportassociationdocumentsarchive a
  JOIN application_sportassociation sa ON sa.sport_association_id=a.sport_association_id
 WHERE a.document_id=$1::uuid ORDER BY a.sport_association_documents_archive_id LIMIT 1`, document)
	if errors.Is(err, sql.ErrNoRows) {
		return "", false, nil
	}
	return owner, err == nil, err
}

// VisibleDocument is visible_documents(request).filter(pk=document).exists():
// documents of the scoped subscriptions (their PDF, their medical certificate's
// document and their files) and of the scoped invoices.
func (r *DocmanagerReads) VisibleDocument(ctx context.Context, scope domain.ResourceScope, document string) (bool, error) {
	query := `WITH scope_parameters AS (SELECT $1::uuid association_id, $2::uuid effective_id, $3::uuid group_id)`
	subscriptions, invoices := "", ""
	if scope.Role == domain.RoleAthlete {
		query += familyScopeCTE
		subscriptions = " AND s.subscription_id IN (SELECT subscription_id FROM visible_subscriptions)"
		invoices = " AND i.invoice_id IN (SELECT invoice_id FROM visible_payments)"
	}
	query += `,
 scoped_subscriptions AS (
  SELECT s.subscription_id, s.document_pdf_id, s.medical_id FROM application_subscription s
  WHERE s.sport_association_id=$1 AND ($3::uuid IS NULL OR s.group_id=$3)` + subscriptions + `
 )
 SELECT EXISTS(SELECT 1 FROM scoped_subscriptions WHERE document_pdf_id=$4::uuid)
     OR EXISTS(SELECT 1 FROM scoped_subscriptions s JOIN application_medicalcertificate m ON m.medical_id=s.medical_id WHERE m.document_id=$4::uuid)
     OR EXISTS(SELECT 1 FROM application_invoice i WHERE i.sport_association_id=$1 AND NOT i.deleted AND ($3::uuid IS NULL OR i.group_id=$3)` + invoices + `
               AND i.document_pdf_id=$4::uuid)
     OR EXISTS(SELECT 1 FROM application_subscriptionfile f JOIN scoped_subscriptions s ON s.subscription_id=f.subscription_id WHERE f.document_id=$4::uuid)`
	var group any
	if scope.GroupID != "" {
		group = scope.GroupID
	}
	var visible bool
	err := r.db.GetContext(ctx, &visible, query, scope.AssociationID, scope.EffectiveID, group, document)
	return visible, err
}

// RenderSource reports whether download_file/view_file would render a missing
// object again: exactly one subscription, or else exactly one invoice, has it
// as its PDF.
func (r *DocmanagerReads) RenderSource(ctx context.Context, document string, group *string) (bool, error) {
	var render bool
	err := r.db.GetContext(ctx, &render, `SELECT
  (SELECT COUNT(*) FROM application_subscription s WHERE s.document_pdf_id=$1::uuid AND ($2::uuid IS NULL OR s.group_id=$2::uuid))=1
  OR (SELECT COUNT(*) FROM application_invoice i WHERE i.document_pdf_id=$1::uuid AND NOT i.deleted AND ($2::uuid IS NULL OR i.group_id=$2::uuid))=1`,
		document, group)
	return render, err
}

// HasActiveAssociation is SportAssociation.objects.filter(user=user).first() is not None.
func (r *DocmanagerReads) HasActiveAssociation(ctx context.Context, user string) (bool, error) {
	var found bool
	err := r.db.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_sportassociation WHERE user_id=$1::uuid AND NOT deleted)`, user)
	return found, err
}
