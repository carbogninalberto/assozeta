package repository

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// RouterCountSubscriptions counts get_queryset() of AssociateSubscriptionViewSet
// without the associate filter (the router's list route has no pk), or the
// one row get_object() looks up when id is set.
func (r *PersonaSubscriptions) RouterCountSubscriptions(ctx context.Context, query domain.PersonaSubscriptionQuery, id *string) (int64, error) {
	var count int64
	err := r.db.GetContext(ctx, &count, `SELECT COUNT(*) FROM application_subscription s WHERE s.sport_association_id=$1 AND NOT s.deleted
 AND ($2::uuid IS NULL OR s.subscription_id=$2::uuid) AND ($3::uuid IS NULL OR s.group_id=$3::uuid)`, query.AssociationID, id, query.GroupID)
	return count, err
}

// RouterSubscriptions is Subscriptions without the associate filter (see
// RouterCountSubscriptions).
func (r *PersonaSubscriptions) RouterSubscriptions(ctx context.Context, query domain.PersonaSubscriptionQuery, id *string, limit, offset int64) ([]domain.PersonaSubscriptionRow, error) {
	rows := []domain.PersonaSubscriptionRow{}
	err := r.db.SelectContext(ctx, &rows, `SELECT s.subscription_id, s.sport_association_id, s.associate_id, s.supplier_id,
 s.instructor_id, s.custom_data, s.additional_fields, s.user_id, s.type, s.role, s.acceptance_date, s.resignation_date, s.medical_id,
 m.expiration_date AS medical_expiration, s.status_flag, s.creation_date, s.start_date, s.end_date, s.signature_url,
 s.signature_storage_key, s.document_pdf_id, s.payment_id, s.archived, s.draft, s.trial, s.meta, s.subscription_number,
 s.subscription_type, s.group_id, s.deleted, s.competitive, s.notes,
 (SELECT string_agg(t.tags_id::text, ',') FROM application_subscription_tags t WHERE t.subscription_id=s.subscription_id) AS tags,
 (s.start_date IS NOT NULL AND s.end_date IS NOT NULL AND NOT (s.start_date <= $4::date AND $4::date <= s.end_date) AND EXISTS(
   SELECT 1 FROM application_subscription f LEFT JOIN application_associate fa ON fa.associate_id=f.associate_id
   WHERE f.sport_association_id=s.sport_association_id AND f.start_date >= s.end_date AND NOT f.archived
   AND ($3::uuid IS NULL OR f.group_id=$3::uuid)
   AND (CASE WHEN a.tax_code IS NULL THEN fa.tax_code IS NULL ELSE UPPER(fa.tax_code::text)=UPPER(a.tax_code::text) END))) AS future_renewal
 FROM application_subscription s
 LEFT JOIN application_medicalcertificate m ON m.medical_id=s.medical_id
 LEFT JOIN application_associate a ON a.associate_id=s.associate_id
 WHERE s.sport_association_id=$1 AND NOT s.deleted AND ($2::uuid IS NULL OR s.subscription_id=$2::uuid) AND ($3::uuid IS NULL OR s.group_id=$3::uuid)
 ORDER BY s.creation_date DESC LIMIT $5 OFFSET $6`, query.AssociationID, id, query.GroupID,
		query.Today.Format("2006-01-02"), limit, offset)
	return rows, err
}
