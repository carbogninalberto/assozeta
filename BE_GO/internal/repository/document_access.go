package repository

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// MemberDocuments is docmanager.access: the base managers (no group or
// soft-delete filter) of the member's subscriptions, payments and documents.
type MemberDocuments struct{ db *sqlx.DB }

func NewMemberDocuments(db *sqlx.DB) MemberDocuments { return MemberDocuments{db: db} }

var _ domain.MemberDocumentStore = MemberDocuments{}

// memberRecordsCTE is member_associates, member_subscriptions and
// member_invoices for the user $1.
const memberRecordsCTE = `WITH own AS (
  SELECT s.associate_id FROM application_subscription s WHERE s.user_id=$1::uuid AND s.associate_id IS NOT NULL
 ), members AS (
  SELECT a.associate_id FROM application_associate a
  WHERE a.user_id=$1::uuid OR a.associate_id IN (SELECT associate_id FROM own)
     OR a.tax_code IN (SELECT o.tax_code FROM application_associate o
                       WHERE o.associate_id IN (SELECT associate_id FROM own) AND o.tax_code IS NOT NULL AND o.tax_code<>'')
 ), member_subscriptions AS (
  SELECT s.subscription_id, s.document_pdf_id, s.medical_id FROM application_subscription s
  WHERE s.user_id=$1::uuid OR s.associate_id IN (SELECT associate_id FROM members)
 ), member_invoices AS (
  SELECT i.invoice_id, i.document_pdf_id FROM application_invoice i
  WHERE i.invoice_id IN (SELECT p.invoice_id FROM application_payment p
                         WHERE p.invoice_id IS NOT NULL AND (p.user_id=$1::uuid OR p.associate_id IN (SELECT associate_id FROM members)))
 )`

func (r MemberDocuments) exists(ctx context.Context, query string, args ...any) (bool, error) {
	var found bool
	err := r.db.GetContext(ctx, &found, query, args...)
	return found, err
}

// InstanceOwner is instance.permissions.is_instance_owner: the first
// configuration is self-hosted and its primary association belongs to the user.
func (r MemberDocuments) InstanceOwner(ctx context.Context, user string) (bool, error) {
	return r.exists(ctx, `SELECT COALESCE((SELECT c.self_hosted AND a.user_id=$1::uuid FROM instance_instanceconfiguration c
  LEFT JOIN application_sportassociation a ON a.sport_association_id=c.primary_association_id ORDER BY c.id LIMIT 1), false)`, user)
}

func (r MemberDocuments) MemberSubscription(ctx context.Context, user, subscription string) (bool, error) {
	return r.exists(ctx, memberRecordsCTE+` SELECT EXISTS(SELECT 1 FROM member_subscriptions WHERE subscription_id=$2::uuid)`, user, subscription)
}

func (r MemberDocuments) MemberInvoice(ctx context.Context, user, invoice string) (bool, error) {
	return r.exists(ctx, memberRecordsCTE+` SELECT EXISTS(SELECT 1 FROM member_invoices WHERE invoice_id=$2::uuid)`, user, invoice)
}

// MemberDocument is member_documents: the PDF, medical certificate document
// and attached files of the member's subscriptions, the certificates of the
// user and the PDFs of the member's invoices.
func (r MemberDocuments) MemberDocument(ctx context.Context, user, document string) (bool, error) {
	return r.exists(ctx, memberRecordsCTE+`
 SELECT EXISTS(SELECT 1 FROM member_subscriptions WHERE document_pdf_id=$2::uuid)
     OR EXISTS(SELECT 1 FROM application_medicalcertificate m WHERE m.document_id=$2::uuid
               AND (m.user_id=$1::uuid OR m.medical_id IN (SELECT medical_id FROM member_subscriptions)))
     OR EXISTS(SELECT 1 FROM application_subscriptionfile f WHERE f.document_id=$2::uuid
               AND f.subscription_id IN (SELECT subscription_id FROM member_subscriptions))
     OR EXISTS(SELECT 1 FROM member_invoices WHERE document_pdf_id=$2::uuid)`, user, document)
}
