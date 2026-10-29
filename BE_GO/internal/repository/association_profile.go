package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type AssociationProfiles struct{ db *sqlx.DB }

func NewAssociationProfiles(db *sqlx.DB) *AssociationProfiles { return &AssociationProfiles{db: db} }

var _ domain.AssociationProfileReader = (*AssociationProfiles)(nil)

func (r *AssociationProfiles) AssociationProfile(ctx context.Context, owner string) (domain.AssociationProfileSnapshot, error) {
	var row domain.AssociationProfileSnapshot
	owned, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{ReadOnly: true, Isolation: sql.LevelRepeatableRead})
	if err != nil {
		return row, err
	}
	defer owned.Rollback()
	tx := owned.tx
	// The association's active manager excludes deleted associations. Django's
	// related User base manager still exposes its inactive/soft-deleted owner.
	err = tx.GetContext(ctx, &row, `SELECT
 a.sport_association_id,
 a.denomination,
 a.tax_code,
 a.address,
 a.address_cap,
 a.address_city,
 a.regulation,
 a.demand,
 a.logo,
 a.subscription_fee::text AS subscription_fee,
 a.multiple_subscription_fee,
 COALESCE(a.subscription_fee_plans,'null'::jsonb) AS subscription_fee_plans,
 a.membership_fee::text AS membership_fee,
 a.multiple_membership_fee,
 COALESCE(a.membership_fee_plans,'null'::jsonb) AS membership_fee_plans,
 COALESCE(a.custom_subscription_form_data,'null'::jsonb) AS custom_subscription_form_data,
 a.document_header,
 a.invoice_footer,
 a.enable_quotes_management,
 COALESCE(a.configuration,'null'::jsonb) AS configuration,
 a.affiliate_code,
 COALESCE(a.additional_sections,'null'::jsonb) AS additional_sections,
 a.president_signature,
 a.stamp,
 a.president_first_name,
 a.president_last_name,
 a.federation,
 a.enroll_number,
 a.sport,
 COALESCE(a.enabled_for,'null'::jsonb) AS enabled_for,
 a.checkout_info,
 COALESCE(a.additional_fields,'null'::jsonb) AS additional_fields,
 COALESCE(a.stripe_available_methods,'null'::jsonb) AS stripe_available_methods,
 a.iban,
 a.website,
 a.abbreviated,
 a.vat_number,
 a.whatsapp,
 a.invoice_template,
 a.subscription_template,
 a.extra_text_invoices,
 a.imported_from_associami,
 a.show_regulation_to_members,
 a.show_regulation_to_both,
 a.show_regulation_to_athletes,
 a.show_demand_to_members,
 a.show_demand_to_both,
 a.show_demand_to_athletes,
 a.user_id,a.reviewed,u.date_joined
 FROM application_sportassociation a JOIN bakney_user u ON u.user_id=a.user_id
 WHERE a.user_id=$1 AND NOT a.deleted`, owner)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.AssociationProfileSnapshot{}, domain.ErrAssociationNotFound
	}
	if err != nil {
		return domain.AssociationProfileSnapshot{}, err
	}
	// Match Django's unsorted Group query and field projection. Do not invent a
	// UUID/name sort or truncate the groups required by the existing response.
	err = tx.SelectContext(ctx, &row.Groups, `SELECT group_id,name,description,sport_association_id
 FROM application_group WHERE sport_association_id=$1`, row.ID)
	if err != nil {
		return domain.AssociationProfileSnapshot{}, err
	}
	if err = owned.Commit(); err != nil {
		return domain.AssociationProfileSnapshot{}, err
	}
	return row, nil
}
