package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type LoginProfiles struct{ db *sqlx.DB }

func NewLoginProfiles(db *sqlx.DB) *LoginProfiles { return &LoginProfiles{db: db} }

var _ domain.LoginProfileReader = (*LoginProfiles)(nil)

func (r *LoginProfiles) LoginProfile(ctx context.Context, id string) (domain.LoginProfileSnapshot, error) {
	var row domain.LoginProfileSnapshot
	owned, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{ReadOnly: true, Isolation: sql.LevelRepeatableRead})
	if err != nil {
		return row, err
	}
	defer owned.Rollback()
	tx := owned.tx
	err = tx.GetContext(ctx, &row, `SELECT
 u.user_id,
 u.first_name,
 u.last_name,
 u.username,
 u.email,
 u.avatar_image,
 u.collaborator_role,
 COALESCE(u.collaborator_permissions,'null'::jsonb) AS collaborator_permissions,
 (u.integration_google_credentials IS NOT NULL AND u.integration_google_credentials <> 'null'::jsonb) AS google_sync_enabled,
 COALESCE(u.dashboard_layout,'null'::jsonb) AS dashboard_layout,
 u.full_installments_plan,
 u.show_zero_payments,
 u.dark_mode,
 u.medical_certificate_notifications,
 u.hide_category_name,
 u.phone,
 u.subscription_duration_equal_sport_year,
 u.balance_sheet_start_day,
 u.balance_sheet_start_month,
 u.balance_sheet_year,
 u.subscription_duration,
 u.membership_duration,
 u.membership_starting_number,
 u.default_membership_type,
 u.subscription_start_day,
 u.subscription_start_month,
 u.custom_end_date,
 u.subscription_end_day,
 u.subscription_end_month,
 u.lead_sport_association_role,
 u.lead_sport_association_size,
 u.lead_sport_market_channel,
 u.default_payment_category_id AS default_payment_category,
 u.default_payment_category_courses_id AS default_payment_category_courses,
 u.role,u.is_active,u.deleted,u.is_superuser,u.date_joined,
 a.sport_association_id,a.logo,a.denomination,a.tax_code,
 config.setup_provenance,config.onboarding_completed_at
 FROM bakney_user u
 LEFT JOIN application_sportassociation a ON a.user_id=u.user_id AND NOT a.deleted
   AND u.role=1 AND NOT u.is_superuser
 LEFT JOIN LATERAL (
  SELECT setup_provenance,onboarding_completed_at
  FROM instance_instanceconfiguration
  WHERE self_hosted AND primary_association_id=a.sport_association_id
  ORDER BY id LIMIT 1
 ) config ON true
 WHERE u.user_id=$1 AND NOT u.deleted`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.LoginProfileSnapshot{}, domain.ErrAccountNotFound
	}
	if err != nil {
		return domain.LoginProfileSnapshot{}, err
	}
	if row.Role == domain.RoleAssociation && !row.Superuser && row.AssociationID != nil {
		err = tx.SelectContext(ctx, &row.Onboarding, `SELECT users_onboarding_id,user_id,show,create_membership,
   view_membership,approve_payment,download_invoice,view_collaborators,view_settings
   FROM application_usersonboarding WHERE user_id=$1 LIMIT 2`, id)
		if err != nil {
			// Classify which read failed; the service decides whether the base
			// snapshot may be returned. Deferred rollback clears the aborted
			// read-only transaction. Never expose partially scanned rows.
			row.Onboarding = nil
			if ctx.Err() != nil {
				return domain.LoginProfileSnapshot{}, ctx.Err()
			}
			return row, errors.Join(domain.ErrProfileOnboardingUnavailable, err)
		}
	}
	if err = owned.Commit(); err != nil {
		return domain.LoginProfileSnapshot{}, err
	}
	return row, nil
}
