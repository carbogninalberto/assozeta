package repository

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type AssociationList struct{ db *sqlx.DB }

func NewAssociationList(db *sqlx.DB) *AssociationList { return &AssociationList{db: db} }

var _ domain.AssociationListStore = (*AssociationList)(nil)

type associationListRow struct {
	domain.AssociationListRow
	domain.AssociationListOnboarding
	OnboardingPresent bool `db:"onboarding_present"`
}

// One query replaces the serializer's per-row user, onboarding, billing and
// notes lookups. Notes follow User.sport_association: a collaborator owner
// resolves its connected user's active association.
const associationListSQL = `SELECT sa.sport_association_id, sa.denomination, sa.tax_code, sa.address, sa.address_cap,
 sa.address_city, u.user_id, u.email, u.username, u.first_name, u.last_name, u.phone, u.lead_sport_association_role,
 u.lead_sport_association_size, u.lead_sport_market_channel, u.date_joined, u.last_login, u.role AS owner_role,
 COALESCE(notes.count, 0) AS notes_count, notes.notes,
 COALESCE(onboarding.count, 0) AS onboarding_count, onboarding.users_onboarding_id IS NOT NULL AS onboarding_present,
 COALESCE(onboarding.users_onboarding_id::text, '') AS users_onboarding_id, COALESCE(onboarding.show, false) AS show,
 COALESCE(onboarding.create_membership, false) AS create_membership, COALESCE(onboarding.view_membership, false) AS view_membership,
 COALESCE(onboarding.approve_payment, false) AS approve_payment, COALESCE(onboarding.download_invoice, false) AS download_invoice,
 COALESCE(onboarding.view_collaborators, false) AS view_collaborators, COALESCE(onboarding.view_settings, false) AS view_settings,
 COALESCE(onboarding.user_id::text, '') AS onboarding_user_id,
 billing.billing_subscription_id IS NOT NULL AS billing_present, billing.auto_renewal, billing.renewal_type, billing.ends_on,
 billing.billing_plan_id
FROM application_sportassociation sa JOIN bakney_user u ON u.user_id=sa.user_id
LEFT JOIN LATERAL (SELECT count(*) AS count, min(n.notes) AS notes FROM application_sportassociation n
 WHERE NOT n.deleted AND n.user_id=CASE WHEN u.role=$2 THEN u.connected_user_id ELSE u.user_id END) notes ON true
LEFT JOIN LATERAL (SELECT o.*, count(*) OVER () AS count FROM application_usersonboarding o WHERE o.user_id=u.user_id LIMIT 1) onboarding ON true
LEFT JOIN LATERAL (SELECT b.* FROM billing_subscription b WHERE b.user_id=u.user_id ORDER BY b.billing_subscription_id LIMIT 1) billing ON true
WHERE NOT sa.deleted AND ($1::uuid IS NULL OR sa.sport_association_id=$1::uuid)`

func (r *AssociationList) AssociationList(ctx context.Context, impersonationAssociation *string) ([]domain.AssociationListRow, error) {
	var rows []associationListRow
	if err := r.db.SelectContext(ctx, &rows, associationListSQL, impersonationAssociation, domain.RoleCollaborator); err != nil {
		return nil, err
	}
	result := make([]domain.AssociationListRow, 0, len(rows))
	for _, row := range rows {
		value := row.AssociationListRow
		if row.OnboardingPresent {
			onboarding := row.AssociationListOnboarding
			value.Onboarding = &onboarding
		}
		result = append(result, value)
	}
	return result, nil
}
