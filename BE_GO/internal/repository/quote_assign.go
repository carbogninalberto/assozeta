package repository

import (
	"context"
	"database/sql"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// QuoteAssigns serves association_quote_assign: the payment writes of the
// partial quotes apply route on job-fenced transactions.
type QuoteAssigns struct{ db *sqlx.DB }

func NewQuoteAssigns(db *sqlx.DB) *QuoteAssigns { return &QuoteAssigns{db: db} }

var _ domain.QuoteAssignStore = (*QuoteAssigns)(nil)

type quoteAssignTransaction struct{ *paymentMiscTransaction }

func (r *QuoteAssigns) BeginQuoteAssign(ctx context.Context, job domain.Job) (domain.QuoteAssignTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	if err = fenceJobEffects(ctx, tx.tx, job.ID, job.LeaseToken); err != nil {
		_ = tx.Rollback()
		return nil, err
	}
	base := &paymentCategoryTransaction{ownedTransaction: tx}
	state := &paymentStateTransaction{paymentArchiveTransaction: &paymentArchiveTransaction{paymentCategoryTransaction: base},
		courses: &courseSubscriptionTransaction{paymentCategoryTransaction: base}}
	return &quoteAssignTransaction{paymentMiscTransaction: &paymentMiscTransaction{
		paymentWritesTransaction: &paymentWritesTransaction{paymentStateTransaction: state}}}, nil
}

// quoteAssignAssociationColumns and quoteAssignSubscriptionColumns are the
// column lists Django selects for the task's querysets (select_related):
// with the same FROM and WHERE they give PostgreSQL the same plan, hence the
// same row order, as Django's queries.
const quoteAssignAssociationColumns = `"application_sportassociation"."sport_association_id", "application_sportassociation"."user_id",
"application_sportassociation"."denomination", "application_sportassociation"."address_city",
"application_sportassociation"."address", "application_sportassociation"."address_cap",
"application_sportassociation"."tax_code", "application_sportassociation"."email", "application_sportassociation"."phone",
"application_sportassociation"."regulation", "application_sportassociation"."demand",
"application_sportassociation"."additional_sections", "application_sportassociation"."logo",
"application_sportassociation"."subscription_fee", "application_sportassociation"."multiple_subscription_fee",
"application_sportassociation"."subscription_fee_plans", "application_sportassociation"."membership_fee",
"application_sportassociation"."multiple_membership_fee", "application_sportassociation"."membership_fee_plans",
"application_sportassociation"."custom_subscription_form_data", "application_sportassociation"."document_header",
"application_sportassociation"."invoice_footer", "application_sportassociation"."enable_quotes_management",
"application_sportassociation"."configuration", "application_sportassociation"."reviewed",
"application_sportassociation"."affiliate_code", "application_sportassociation"."affiliate_code_stripe",
"application_sportassociation"."deleted", "application_sportassociation"."review_url",
"application_sportassociation"."review_url_enabled", "application_sportassociation"."crm_user_id",
"application_sportassociation"."crm_contact_id", "application_sportassociation"."president_signature",
"application_sportassociation"."stamp", "application_sportassociation"."president_first_name",
"application_sportassociation"."president_last_name", "application_sportassociation"."enroll_number",
"application_sportassociation"."federation", "application_sportassociation"."sport",
"application_sportassociation"."enabled_for", "application_sportassociation"."checkout_info",
"application_sportassociation"."additional_fields", "application_sportassociation"."stripe_available_methods",
"application_sportassociation"."iban", "application_sportassociation"."website",
"application_sportassociation"."abbreviated", "application_sportassociation"."vat_number",
"application_sportassociation"."whatsapp", "application_sportassociation"."imported_from_associami",
"application_sportassociation"."extra_text_invoices", "application_sportassociation"."show_regulation_to_members",
"application_sportassociation"."show_regulation_to_both", "application_sportassociation"."show_regulation_to_athletes",
"application_sportassociation"."show_demand_to_members", "application_sportassociation"."show_demand_to_both",
"application_sportassociation"."show_demand_to_athletes", "application_sportassociation"."invoice_template",
"application_sportassociation"."subscription_template", "application_sportassociation"."notes", "bakney_user"."password",
"bakney_user"."last_login", "bakney_user"."is_superuser", "bakney_user"."username", "bakney_user"."first_name",
"bakney_user"."last_name", "bakney_user"."email", "bakney_user"."is_staff", "bakney_user"."is_active",
"bakney_user"."date_joined", "bakney_user"."user_id", "bakney_user"."role", "bakney_user"."avatar_image",
"bakney_user"."phone", "bakney_user"."two_fa", "bakney_user"."two_fa_secret", "bakney_user"."stripe_account_id",
"bakney_user"."stripe_on_boarding_completed", "bakney_user"."enumerate_invoices", "bakney_user"."online_payments",
"bakney_user"."balance_sheet_year", "bakney_user"."balance_sheet_start_day", "bakney_user"."balance_sheet_start_month",
"bakney_user"."temporary_invoice_deletion", "bakney_user"."auto_archive", "bakney_user"."auto_mark_attendance",
"bakney_user"."full_installments_plan", "bakney_user"."payment_date_equal_invoice_date",
"bakney_user"."starting_number_invoices", "bakney_user"."connected_user_id", "bakney_user"."auto_paid_payment",
"bakney_user"."tables_settings", "bakney_user"."deleted", "bakney_user"."delete_on", "bakney_user"."collaborator_role",
"bakney_user"."collaborator_permissions", "bakney_user"."dark_mode", "bakney_user"."integration_google_state",
"bakney_user"."integration_google_credentials", "bakney_user"."integration_google_auto_sync",
"bakney_user"."dashboard_layout", "bakney_user"."subscription_duration_equal_sport_year",
"bakney_user"."subscription_duration", "bakney_user"."membership_duration", "bakney_user"."subscription_start_day",
"bakney_user"."subscription_start_month", "bakney_user"."custom_end_date", "bakney_user"."subscription_end_day",
"bakney_user"."subscription_end_month", "bakney_user"."membership_starting_number", "bakney_user"."default_membership_type",
"bakney_user"."lead_sport_association_role", "bakney_user"."lead_sport_association_size",
"bakney_user"."lead_sport_market_channel", "bakney_user"."disable_account_creation", "bakney_user"."force_account_creation",
"bakney_user"."medical_certificate_notifications", "bakney_user"."hide_category_name", "bakney_user"."show_zero_payments",
"bakney_user"."imported_from_associami", "bakney_user"."default_payment_category_id",
"bakney_user"."default_payment_category_courses_id"`

const quoteAssignSubscriptionColumns = `"application_subscription"."subscription_id", "application_subscription"."sport_association_id",
"application_subscription"."associate_id", "application_subscription"."supplier_id",
"application_subscription"."instructor_id", "application_subscription"."custom_data",
"application_subscription"."additional_fields", "application_subscription"."user_id", "application_subscription"."type",
"application_subscription"."role", "application_subscription"."acceptance_date",
"application_subscription"."resignation_date", "application_subscription"."medical_id",
"application_subscription"."status_flag", "application_subscription"."creation_date",
"application_subscription"."start_date", "application_subscription"."end_date", "application_subscription"."signature_url",
"application_subscription"."signature_storage_key", "application_subscription"."document_pdf_id",
"application_subscription"."payment_id", "application_subscription"."archived", "application_subscription"."draft",
"application_subscription"."trial", "application_subscription"."meta", "application_subscription"."subscription_number",
"application_subscription"."subscription_type", "application_subscription"."group_id", "application_subscription"."deleted",
"application_subscription"."competitive", "application_subscription"."notes", "application_associate"."associate_id",
"application_associate"."user_id", "application_associate"."sport_association_id", "application_associate"."picture_path",
"application_associate"."first_name", "application_associate"."last_name", "application_associate"."sex",
"application_associate"."tax_code", "application_associate"."born_date", "application_associate"."born_city",
"application_associate"."address_city", "application_associate"."address", "application_associate"."address_cap",
"application_associate"."nationality", "application_associate"."nationality_residence", "application_associate"."email",
"application_associate"."phone", "application_associate"."phone_2", "application_associate"."phone_3",
"application_associate"."phone_4", "application_associate"."phone_label", "application_associate"."phone_2_label",
"application_associate"."phone_3_label", "application_associate"."phone_4_label", "application_associate"."is_minor",
"application_associate"."disabled", "application_associate"."draft", "application_associate"."group_id",
"application_associate"."deleted", "application_associate"."family_id", "application_associate"."family_role",
"application_associate"."id_number", "application_associate"."creation_date", "application_associate"."migrated",
"application_associate"."notes", "bakney_user"."password", "bakney_user"."last_login", "bakney_user"."is_superuser",
"bakney_user"."username", "bakney_user"."first_name", "bakney_user"."last_name", "bakney_user"."email",
"bakney_user"."is_staff", "bakney_user"."is_active", "bakney_user"."date_joined", "bakney_user"."user_id",
"bakney_user"."role", "bakney_user"."avatar_image", "bakney_user"."phone", "bakney_user"."two_fa",
"bakney_user"."two_fa_secret", "bakney_user"."stripe_account_id", "bakney_user"."stripe_on_boarding_completed",
"bakney_user"."enumerate_invoices", "bakney_user"."online_payments", "bakney_user"."balance_sheet_year",
"bakney_user"."balance_sheet_start_day", "bakney_user"."balance_sheet_start_month",
"bakney_user"."temporary_invoice_deletion", "bakney_user"."auto_archive", "bakney_user"."auto_mark_attendance",
"bakney_user"."full_installments_plan", "bakney_user"."payment_date_equal_invoice_date",
"bakney_user"."starting_number_invoices", "bakney_user"."connected_user_id", "bakney_user"."auto_paid_payment",
"bakney_user"."tables_settings", "bakney_user"."deleted", "bakney_user"."delete_on", "bakney_user"."collaborator_role",
"bakney_user"."collaborator_permissions", "bakney_user"."dark_mode", "bakney_user"."integration_google_state",
"bakney_user"."integration_google_credentials", "bakney_user"."integration_google_auto_sync",
"bakney_user"."dashboard_layout", "bakney_user"."subscription_duration_equal_sport_year",
"bakney_user"."subscription_duration", "bakney_user"."membership_duration", "bakney_user"."subscription_start_day",
"bakney_user"."subscription_start_month", "bakney_user"."custom_end_date", "bakney_user"."subscription_end_day",
"bakney_user"."subscription_end_month", "bakney_user"."membership_starting_number", "bakney_user"."default_membership_type",
"bakney_user"."lead_sport_association_role", "bakney_user"."lead_sport_association_size",
"bakney_user"."lead_sport_market_channel", "bakney_user"."disable_account_creation", "bakney_user"."force_account_creation",
"bakney_user"."medical_certificate_notifications", "bakney_user"."hide_category_name", "bakney_user"."show_zero_payments",
"bakney_user"."imported_from_associami", "bakney_user"."default_payment_category_id",
"bakney_user"."default_payment_category_courses_id", "application_payment"."payment_id", "application_payment"."type",
"application_payment"."subject", "application_payment"."amount", "application_payment"."creation_date",
"application_payment"."payment_date", "application_payment"."paid", "application_payment"."expense",
"application_payment"."description", "application_payment"."payment_category_id",
"application_payment"."meta_payment_categories", "application_payment"."invoice_id", "application_payment"."user_id",
"application_payment"."associate_id", "application_payment"."supplier_id", "application_payment"."instructor_id",
"application_payment"."sport_association_id", "application_payment"."payment_intent_id", "application_payment"."archived",
"application_payment"."invoice_fiscal_code", "application_payment"."meta", "application_payment"."notes",
"application_payment"."custom_accounts_id", "application_payment"."signature_id", "application_payment"."deleted",
"application_payment"."group_id", "application_payment"."course"`

// QuoteAssignAssociations declares the server-side cursor Django's
// .iterator(chunk_size=50) declares (a cursor is planned for a fast start,
// which decides the row order) and fetches it in chunks of 50; the fields
// the task reads are then loaded by key.
func (t *quoteAssignTransaction) QuoteAssignAssociations(ctx context.Context) ([]domain.QuoteAssignAssociation, error) {
	if _, err := t.tx.ExecContext(ctx, `DECLARE "tasks_db_f_quote_assign" NO SCROLL CURSOR FOR SELECT `+quoteAssignAssociationColumns+`
 FROM "application_sportassociation" INNER JOIN "bakney_user" ON ("application_sportassociation"."user_id" = "bakney_user"."user_id")
 WHERE (NOT "application_sportassociation"."deleted" AND NOT ("application_sportassociation"."subscription_fee_plans" IS NULL)
 AND NOT ("application_sportassociation"."subscription_fee_plans" = '[]'::jsonb) AND "application_sportassociation"."multiple_subscription_fee"
 AND UPPER("application_sportassociation"."subscription_fee_plans"::text) LIKE UPPER('%"advanced\_options": true%'))`); err != nil {
		return nil, err
	}
	ids := []string{}
	for {
		chunk, err := tasksDBFFirstColumn(ctx, t.tx, `FETCH FORWARD 50 FROM "tasks_db_f_quote_assign"`)
		if err != nil {
			return nil, err
		}
		ids = append(ids, chunk...)
		if len(chunk) < 50 {
			break
		}
	}
	if _, err := t.tx.ExecContext(ctx, `CLOSE "tasks_db_f_quote_assign"`); err != nil {
		return nil, err
	}
	out := make([]domain.QuoteAssignAssociation, 0, len(ids))
	for _, id := range ids {
		var row domain.QuoteAssignAssociation
		if err := t.tx.GetContext(ctx, &row, `SELECT a.sport_association_id, a.user_id, a.denomination,
 a.subscription_fee_plans::text AS subscription_fee_plans, u.email AS owner_email, u.balance_sheet_start_day, u.balance_sheet_start_month
 FROM application_sportassociation a JOIN bakney_user u ON u.user_id=a.user_id WHERE a.sport_association_id=$1::uuid`, id); err != nil {
			return nil, err
		}
		out = append(out, row)
	}
	return out, nil
}

// QuoteAssignSubscriptions issues Django's query for the task's queryset
// (select_related payment, associate and user; no ORDER BY) for the row
// order, then loads the fields the task reads by key.
func (t *quoteAssignTransaction) QuoteAssignSubscriptions(ctx context.Context, association string, from, to time.Time, pattern string) (
	[]domain.QuoteAssignSubscription, error) {
	ids, err := tasksDBFFirstColumn(ctx, t.tx, `SELECT `+quoteAssignSubscriptionColumns+`
 FROM "application_subscription" INNER JOIN "application_payment" ON ("application_subscription"."payment_id" = "application_payment"."payment_id")
 LEFT OUTER JOIN "application_associate" ON ("application_subscription"."associate_id" = "application_associate"."associate_id")
 INNER JOIN "bakney_user" ON ("application_subscription"."user_id" = "bakney_user"."user_id")
 WHERE (NOT "application_subscription"."archived" AND "application_subscription"."creation_date" >= $2
 AND "application_subscription"."creation_date" <= $3 AND NOT "application_subscription"."deleted"
 AND "application_subscription"."meta" IS NOT NULL AND "application_subscription"."payment_id" IS NOT NULL
 AND "application_payment"."meta" IS NOT NULL AND "application_subscription"."sport_association_id" = $1::uuid
 AND NOT ("application_subscription"."meta" = '"{\"plan_id\": null}"'::jsonb AND "application_subscription"."meta" IS NOT NULL)
 AND UPPER("application_payment"."meta"::text) LIKE UPPER($4))`, association, from, to, pattern)
	if err != nil {
		return nil, err
	}
	rows := make([]domain.QuoteAssignSubscription, 0, len(ids))
	for _, id := range ids {
		var row domain.QuoteAssignSubscription
		if err := t.tx.GetContext(ctx, &row, `SELECT s.subscription_id, s.user_id, u.email AS user_email, s.associate_id,
 a.first_name AS associate_first_name, a.last_name AS associate_last_name
 FROM application_subscription s JOIN bakney_user u ON u.user_id=s.user_id
 LEFT JOIN application_associate a ON a.associate_id=s.associate_id WHERE s.subscription_id=$1::uuid`, id); err != nil {
			return nil, err
		}
		rows = append(rows, row)
	}
	return rows, nil
}

// tasksDBFFirstColumn runs a query and returns its first column as text.
func tasksDBFFirstColumn(ctx context.Context, tx *sqlx.Tx, query string, args ...any) ([]string, error) {
	rows, err := tx.QueryxContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []string{}
	for rows.Next() {
		values, err := rows.SliceScan()
		if err != nil {
			return nil, err
		}
		out = append(out, tasksDBFText(values[0]))
	}
	return out, rows.Err()
}

func (t *quoteAssignTransaction) CompleteJob(ctx context.Context, id, token uuid.UUID) error {
	return completeJob(ctx, t.tx, id, token, nil)
}
