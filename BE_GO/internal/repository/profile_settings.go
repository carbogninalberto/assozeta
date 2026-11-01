package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// ProfileSettings stores profile_settings and profile_integrations writes.
// Every service step runs in its own transaction, like Django's autocommit.
type ProfileSettings struct{ db *sqlx.DB }

func NewProfileSettings(db *sqlx.DB) *ProfileSettings { return &ProfileSettings{db: db} }

var _ domain.ProfileSettingsStore = (*ProfileSettings)(nil)

type profileSettingsTransaction struct{ *ownedTransaction }

func (r *ProfileSettings) BeginProfileSettings(ctx context.Context) (domain.ProfileSettingsTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &profileSettingsTransaction{ownedTransaction: tx}, nil
}

func (t *profileSettingsTransaction) OwnerAssociation(ctx context.Context, owner string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, owner, nil, false)
}

const profileSettingsColumns = `enumerate_invoices,online_payments,balance_sheet_year,balance_sheet_start_day,balance_sheet_start_month,
 temporary_invoice_deletion,auto_archive,auto_mark_attendance,payment_date_equal_invoice_date,starting_number_invoices,
 auto_paid_payment,full_installments_plan,show_zero_payments,dark_mode,medical_certificate_notifications,hide_category_name,
 subscription_duration_equal_sport_year,subscription_duration,membership_duration,membership_starting_number,
 default_membership_type,subscription_start_day,subscription_start_month,custom_end_date,subscription_end_day,
 subscription_end_month,disable_account_creation,force_account_creation,default_payment_category_id,
 default_payment_category_courses_id`

func (t *profileSettingsTransaction) User(ctx context.Context, owner string, lock bool) (domain.ProfileSettingsUser, error) {
	query := `SELECT user_id,first_name,last_name,COALESCE(tables_settings,'null'::jsonb) AS tables_settings,` + profileSettingsColumns + `
 FROM bakney_user WHERE user_id=$1`
	if lock {
		query += ` FOR UPDATE`
	}
	var row domain.ProfileSettingsUser
	err := t.tx.GetContext(ctx, &row, query, owner)
	if errors.Is(err, sql.ErrNoRows) {
		return row, domain.ErrAccountNotFound
	}
	return row, err
}

func (t *profileSettingsTransaction) PaymentCategory(ctx context.Context, id string) (bool, *string, error) {
	var association *string
	err := t.tx.GetContext(ctx, &association, `SELECT sport_association_id FROM application_paymentcategory WHERE payment_category_id=$1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil, nil
	}
	return err == nil, association, err
}

// SaveSettings is the 30 columns of user.save(); the other columns hold the
// values Django loaded for request.user.
func (t *profileSettingsTransaction) SaveSettings(ctx context.Context, owner string, v domain.ProfileSettings) error {
	result, err := t.tx.ExecContext(ctx, `UPDATE bakney_user SET (`+profileSettingsColumns+`)=
 ($2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31) WHERE user_id=$1`,
		owner, v.EnumerateInvoices, v.OnlinePayments, v.BalanceSheetYear, v.BalanceSheetStartDay, v.BalanceSheetStartMonth,
		v.TemporaryInvoiceDeletion, v.AutoArchive, v.AutoMarkAttendance, v.PaymentDateEqualInvoiceDate, v.StartingNumberInvoices,
		v.AutoPaidPayment, v.FullInstallmentsPlan, v.ShowZeroPayments, v.DarkMode, v.MedicalCertificateNotifications, v.HideCategoryName,
		v.SubscriptionDurationEqualSportYear, v.SubscriptionDuration, v.MembershipDuration, v.MembershipStartingNumber,
		v.DefaultMembershipType, v.SubscriptionStartDay, v.SubscriptionStartMonth, v.CustomEndDate, v.SubscriptionEndDay,
		v.SubscriptionEndMonth, v.DisableAccountCreation, v.ForceAccountCreation, v.DefaultPaymentCategory, v.DefaultPaymentCategoryCourses)
	if err != nil {
		return err
	}
	if count, err := result.RowsAffected(); err != nil || count != 1 {
		return errors.Join(domain.ErrAccountNotFound, err)
	}
	return nil
}

func (t *profileSettingsTransaction) FirstCard(ctx context.Context, association string) (*domain.MembershipCardConfiguration, error) {
	var row domain.MembershipCardConfiguration
	err := t.tx.GetContext(ctx, &row, `SELECT sport_association_membership_card_configuration_id,sport_association_id,
 emit_only_on_approval,COALESCE(customized_template,'null'::jsonb) AS customized_template
 FROM application_sportassociationmembershipcardconfiguration WHERE sport_association_id=$1
 ORDER BY sport_association_membership_card_configuration_id LIMIT 1 FOR UPDATE`, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// SaveCard writes a nil template as SQL NULL (JSONField(null=True) and None).
func (t *profileSettingsTransaction) SaveCard(ctx context.Context, id string, emit bool, template json.RawMessage) error {
	var stored any
	if template != nil {
		stored = string(template)
	}
	_, err := t.tx.ExecContext(ctx, `UPDATE application_sportassociationmembershipcardconfiguration
 SET emit_only_on_approval=$2, customized_template=$3::jsonb WHERE sport_association_membership_card_configuration_id=$1`, id, emit, stored)
	return err
}

func (t *profileSettingsTransaction) Review(ctx context.Context, association string, lock bool) (domain.ProfileReview, error) {
	query := `SELECT review_url,review_url_enabled FROM application_sportassociation WHERE sport_association_id=$1`
	if lock {
		query += ` FOR UPDATE`
	}
	var row domain.ProfileReview
	err := t.tx.GetContext(ctx, &row, query, association)
	if errors.Is(err, sql.ErrNoRows) {
		return row, domain.ErrAssociationNotFound
	}
	return row, err
}

func (t *profileSettingsTransaction) SaveReview(ctx context.Context, association string, url *string, enabled bool) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_sportassociation SET review_url=$2, review_url_enabled=$3 WHERE sport_association_id=$1`,
		association, url, enabled)
	return err
}

func (t *profileSettingsTransaction) AppendAudit(ctx context.Context, event domain.ProfileSettingsAudit) error {
	association, path := event.AssociationID, event.ResolutionPath
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: event.Model, Key: event.Key, Representation: event.Representation,
		Action: event.Action, Changes: event.Changes, Context: event.Context, AssociationID: &association, ResolutionPath: &path, At: event.At})
}
