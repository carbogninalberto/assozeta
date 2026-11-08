package repository

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type PersonaSubscriptions struct{ db *sqlx.DB }

func NewPersonaSubscriptions(db *sqlx.DB) *PersonaSubscriptions { return &PersonaSubscriptions{db: db} }

var _ domain.PersonaSubscriptionsStore = (*PersonaSubscriptions)(nil)

// Subscription.objects (GroupAwareManager) of the association, not deleted.
const personaSubscriptionsWhere = ` FROM application_subscription s
 WHERE s.sport_association_id=$1 AND NOT s.deleted AND s.associate_id=$2 AND ($3::uuid IS NULL OR s.group_id=$3::uuid)`

func (r *PersonaSubscriptions) CountSubscriptions(ctx context.Context, query domain.PersonaSubscriptionQuery) (int64, error) {
	var count int64
	err := r.db.GetContext(ctx, &count, `SELECT COUNT(*)`+personaSubscriptionsWhere, query.AssociationID, query.AssociateID, query.GroupID)
	return count, err
}

// Subscriptions orders by -creation_date. has_future_renewal() runs only when
// the row is not current (both dates set and today outside them): later
// subscriptions (Subscription.objects: group-aware, any deleted state)
// of the association, not archived, whose associate's tax code matches
// iexact (None: IS NULL, through a LEFT JOIN).
func (r *PersonaSubscriptions) Subscriptions(ctx context.Context, query domain.PersonaSubscriptionQuery, limit, offset int64) ([]domain.PersonaSubscriptionRow, error) {
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
 WHERE s.sport_association_id=$1 AND NOT s.deleted AND s.associate_id=$2 AND ($3::uuid IS NULL OR s.group_id=$3::uuid)
 ORDER BY s.creation_date DESC LIMIT $5 OFFSET $6`, query.AssociationID, query.AssociateID, query.GroupID,
		query.Today.Format("2006-01-02"), limit, offset)
	return rows, err
}

func (r *PersonaSubscriptions) SubscriptionVisible(ctx context.Context, id, association string, group *string) (bool, error) {
	var found bool
	err := r.db.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_subscription WHERE subscription_id=$1
 AND sport_association_id=$2 AND NOT deleted AND ($3::uuid IS NULL OR group_id=$3::uuid))`, id, association, group)
	return found, err
}
