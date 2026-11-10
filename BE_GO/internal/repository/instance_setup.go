package repository

import (
	"context"
	"database/sql"
	"errors"
	"sort"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// InstanceSetup stores the first-run configuration (InstanceSetupView), its
// partial updates (InstanceReconfigureView) and the logo path
// (InstanceLogoUploadView). InstanceConfiguration is not registered with
// auditlog; the setup's User, SportAssociation, CustomAccounts and
// UsersOnboarding rows are, so their create entries and index rows are
// written in the same transaction.
type InstanceSetup struct{ db *sqlx.DB }

func NewInstanceSetup(db *sqlx.DB) *InstanceSetup { return &InstanceSetup{db: db} }

// First is InstanceConfiguration.objects.select_related('primary_association').first().
func (r *InstanceSetup) First(ctx context.Context) (*domain.InstanceSetupConfiguration, error) {
	var row domain.InstanceSetupConfiguration
	err := r.db.GetContext(ctx, &row, `SELECT c.id, a.user_id::text AS owner_id FROM instance_instanceconfiguration c
 LEFT JOIN application_sportassociation a ON a.sport_association_id=c.primary_association_id ORDER BY c.id LIMIT 1`)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// ImportTask is the django-celery-results row AsyncResult(task_id) reads
// (nil: no stored result, PENDING).
func (r *InstanceSetup) ImportTask(ctx context.Context, task string) (*domain.AssociationTaskResult, error) {
	var row domain.AssociationTaskResult
	err := r.db.GetContext(ctx, &row, `SELECT status, result, meta, content_encoding FROM django_celery_results_taskresult WHERE task_id=$1`, task)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// ImportedAssociation is SportAssociation.original_objects.filter(pk=id).first() (deleted rows included).
func (r *InstanceSetup) ImportedAssociation(ctx context.Context, id string) (bool, error) {
	var found bool
	err := r.db.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_sportassociation WHERE sport_association_id=$1::uuid)`, id)
	return found, err
}

// LatestAssociation is SportAssociation.original_objects.order_by('-sport_association_id').first().
func (r *InstanceSetup) LatestAssociation(ctx context.Context) (*string, error) {
	var ids []string
	if err := r.db.SelectContext(ctx, &ids, `SELECT sport_association_id::text FROM application_sportassociation
 ORDER BY sport_association_id DESC LIMIT 1`); err != nil {
		return nil, err
	}
	if len(ids) == 0 {
		return nil, nil
	}
	return &ids[0], nil
}

// CreateImported is the transaction.atomic block of InstanceSetupView.post
// for an import setup: InstanceConfiguration.objects.create (save's
// singleton check, setup_provenance 'import', onboarding completed now),
// then config.save() with the imported association.
func (r *InstanceSetup) CreateImported(ctx context.Context, input domain.InstanceSetupImported, now func() time.Time) error {
	tx, err := r.db.BeginTxx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var exists bool
	if err = tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM instance_instanceconfiguration)`); err != nil {
		return err
	}
	if exists {
		return domain.ErrInstanceSetupExists
	}
	completed := now()
	created := now()
	var id int64
	if err = tx.GetContext(ctx, &id, `INSERT INTO instance_instanceconfiguration
 (domain,configured_at,updated_at,setup_provenance,onboarding_completed_at,name,abbreviation,primary_color,support_email,logo_path,
  email_settings,email_password_encrypted,email_revision,diagnostic_results,integration_settings,display_settings,is_reseller,
  self_hosted,support_multiple_associations,show_course_athletes,show_minor_exemption,show_tutorial,google_client_id,apple_client_id,
  stripe_public_key,stripe_secret_key,stripe_webhook_secret,stripe_pricing_table,stripe_client_portal,meta_title,meta_description,
  primary_association_id,ui_api_version)
 VALUES ($1,$2,$2,'import',$3,$4,$5,$6,$7,$8,'{}'::jsonb,'',0,'{}'::jsonb,'{}'::jsonb,$9::jsonb,FALSE,TRUE,FALSE,TRUE,TRUE,TRUE,$10,$11,
  '','','','','','','',NULL,'v1') RETURNING id`,
		input.Domain, created, completed, input.Name, input.Abbreviation, input.PrimaryColor, input.SupportEmail, input.LogoPath,
		instanceSetupDisplay, input.GoogleClientID, input.AppleClientID); err != nil {
		return err
	}
	// config.save(): every field written again; only these differ.
	if _, err = tx.ExecContext(ctx, `UPDATE instance_instanceconfiguration SET primary_association_id=$2, updated_at=$3 WHERE id=$1`,
		id, input.AssociationID, now()); err != nil {
		return err
	}
	return tx.Commit()
}

// instanceSetupDisplay is json.dumps(DEFAULT_DISPLAY_SETTINGS) (BE/instance/defaults.py).
const instanceSetupDisplay = `{"general": {"searchBar": true, "cookieConsent": true, "showDemoButton": false}, ` +
	`"login": {"showSportAssociationCreation": false, "allowOauthLogin": true, "showFooterInfo": true, "allowOnlyAthletes": false, ` +
	`"showTestimonials": false, "termsAndConditionsUrl": ""}, "navbar": {"showAffiliateLink": false, "showReview": false, ` +
	`"showNotifications": true}, "sidebar": {"showPlanUpgrades": false, "showPrivacyPolicy": true, "showTermsOfService": true, ` +
	`"showPreferences": true}}`

// InstanceSetupDisplayDefaults is DEFAULT_DISPLAY_SETTINGS as stored JSON.
func InstanceSetupDisplayDefaults() string { return instanceSetupDisplay }

// CreateFresh is the transaction.atomic block of InstanceSetupView.post for
// a fresh setup, in Django's order: InstanceConfiguration.objects.create
// (save's singleton check), User.objects.create_user, SportAssociation,
// the 'cassa' and 'banca' CustomAccounts, UsersOnboarding, then config.save()
// with the primary association. Each audited create writes its auditlog
// entry (and, where the resolver finds an association, its index row) right
// after its insert.
func (r *InstanceSetup) CreateFresh(ctx context.Context, fresh domain.InstanceSetupFresh, audit domain.LoginAuditContext, now func() time.Time) error {
	tx, err := r.db.BeginTxx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var exists bool
	if err = tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM instance_instanceconfiguration)`); err != nil {
		return err
	}
	if exists {
		return domain.ErrInstanceSetupExists
	}
	created := now()
	var id int64
	if err = tx.GetContext(ctx, &id, `INSERT INTO instance_instanceconfiguration
 (domain,configured_at,updated_at,setup_provenance,onboarding_completed_at,name,abbreviation,primary_color,support_email,logo_path,
  email_settings,email_password_encrypted,email_revision,diagnostic_results,integration_settings,display_settings,is_reseller,
  self_hosted,support_multiple_associations,show_course_athletes,show_minor_exemption,show_tutorial,google_client_id,apple_client_id,
  stripe_public_key,stripe_secret_key,stripe_webhook_secret,stripe_pricing_table,stripe_client_portal,meta_title,meta_description,
  primary_association_id,ui_api_version)
 VALUES ($1,$2,$2,'fresh',NULL,$3,$4,$5,$6,$7,'{}'::jsonb,'',0,'{}'::jsonb,'{}'::jsonb,$8::jsonb,FALSE,TRUE,FALSE,TRUE,TRUE,TRUE,$9,$10,
  '','','','','','','',NULL,'v1') RETURNING id`,
		fresh.Domain, created, fresh.Name, fresh.Abbreviation, fresh.PrimaryColor, fresh.SupportEmail, fresh.LogoPath, instanceSetupDisplay,
		fresh.GoogleClientID, fresh.AppleClientID); err != nil {
		return err
	}
	joined := now()
	if _, err = tx.ExecContext(ctx, `INSERT INTO bakney_user
 (password,last_login,is_superuser,username,first_name,last_name,email,is_staff,is_active,date_joined,user_id,role,two_fa,
  stripe_on_boarding_completed,enumerate_invoices,online_payments,balance_sheet_year,balance_sheet_start_day,balance_sheet_start_month,
  temporary_invoice_deletion,auto_archive,auto_mark_attendance,full_installments_plan,payment_date_equal_invoice_date,
  starting_number_invoices,auto_paid_payment,tables_settings,deleted,collaborator_role,dark_mode,integration_google_auto_sync,
  subscription_duration_equal_sport_year,subscription_duration,membership_duration,subscription_start_day,subscription_start_month,
  custom_end_date,subscription_end_day,subscription_end_month,membership_starting_number,default_membership_type,
  disable_account_creation,force_account_creation,medical_certificate_notifications,hide_category_name,show_zero_payments,
  imported_from_associami)
 VALUES ($1,NULL,FALSE,$2,'','',$3,FALSE,TRUE,$4,$5,1,FALSE,FALSE,FALSE,TRUE,1,1,1,TRUE,FALSE,FALSE,FALSE,FALSE,0,FALSE,'{}'::jsonb,
  FALSE,1,FALSE,FALSE,TRUE,2,2,1,1,FALSE,31,12,0,'',FALSE,FALSE,TRUE,FALSE,FALSE,FALSE)`,
		fresh.PasswordHash, fresh.Username, fresh.OwnerEmail, joined, fresh.UserID); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, tx, "bakney_user", "user_id", fresh.UserID)
	if err != nil {
		return err
	}
	// auditlog.register(User, exclude_fields=['last_login', 'password']);
	// reverse foreign keys with a related_name render as their managers. The
	// owner has no association yet and the request has no actor, so the
	// entry has no index row.
	delete(changes, "password")
	delete(changes, "last_login")
	for name, manager := range map[string]string{"requester": "application.SubscriptionTransfer.None", "recipient": "application.SubscriptionTransfer.None",
		"saved_reports": "application.SavedReport.None", "social_auth": "social_django.UserSocialAuth.None"} {
		changes[name] = [2]string{"None", manager}
	}
	representation := "name:  " // User.__str__ with empty first and last names
	if err = r.audit(ctx, tx, "user", fresh.UserID, representation, changes, audit, nil, "", now); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO application_sportassociation
 (sport_association_id,user_id,denomination,tax_code,additional_sections,subscription_fee,multiple_subscription_fee,subscription_fee_plans,
  membership_fee,multiple_membership_fee,membership_fee_plans,enable_quotes_management,configuration,reviewed,deleted,review_url_enabled,
  enabled_for,stripe_available_methods,imported_from_associami,show_regulation_to_members,show_regulation_to_both,
  show_regulation_to_athletes,show_demand_to_members,show_demand_to_both,show_demand_to_athletes,invoice_template,subscription_template)
 VALUES ($1,$2,$3,'',$4::jsonb,20.00,FALSE,'[]'::jsonb,20.00,FALSE,'[]'::jsonb,TRUE,
  '{"mandatory_phone": false, "mandatory_email": false, "mandatory_signature": false}'::jsonb,FALSE,FALSE,FALSE,
  '["associate", "associate-membership", "membership"]'::jsonb,'["card", "sepa_debit"]'::jsonb,FALSE,TRUE,TRUE,TRUE,TRUE,TRUE,TRUE,
  'invoice.html','subscription.html')`, fresh.AssociationID, fresh.UserID, fresh.AssociationName, instanceSetupAdditionalSections); err != nil {
		return err
	}
	if changes, err = createdModelChanges(ctx, tx, "application_sportassociation", "sport_association_id", fresh.AssociationID); err != nil {
		return err
	}
	changes["instance_config"] = [2]string{"None", "instance.InstanceConfiguration.None"}
	if err = r.audit(ctx, tx, "sportassociation", fresh.AssociationID, "SportAssociation object ("+fresh.AssociationID+")", changes, audit,
		&fresh.AssociationID, "self", now); err != nil {
		return err
	}
	for _, account := range []struct {
		id, name string
		kind     int
	}{{fresh.CashAccountID, "cassa", 1}, {fresh.BankAccountID, "banca", 2}} {
		if _, err = tx.ExecContext(ctx, `INSERT INTO application_customaccounts
 (custom_account_id,enabled,name,initial_balance,account_type,account_code,sport_association_id,editable)
 VALUES ($1,TRUE,$2,0,$3,$2,$4,FALSE)`, account.id, account.name, account.kind, fresh.AssociationID); err != nil {
			return err
		}
		if changes, err = createdModelChanges(ctx, tx, "application_customaccounts", "custom_account_id", account.id); err != nil {
			return err
		}
		// initial_balance=0 is the int 0 in memory when auditlog renders it.
		changes["initial_balance"] = [2]string{"None", "0"}
		manager := [2]string{"None", "application.CustomAccountsTransfer.None"}
		changes["custom_account_from"], changes["custom_account_to"] = manager, manager
		if err = r.audit(ctx, tx, "customaccounts", account.id, "CustomAccounts object ("+account.id+")", changes, audit,
			&fresh.AssociationID, "direct", now); err != nil {
			return err
		}
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO application_usersonboarding
 (users_onboarding_id,user_id,show,create_membership,view_membership,approve_payment,download_invoice,view_collaborators,view_settings)
 VALUES ($1,$2,TRUE,FALSE,FALSE,FALSE,FALSE,FALSE,FALSE)`, fresh.OnboardingID, fresh.UserID); err != nil {
		return err
	}
	if changes, err = createdModelChanges(ctx, tx, "application_usersonboarding", "users_onboarding_id", fresh.OnboardingID); err != nil {
		return err
	}
	if err = r.audit(ctx, tx, "usersonboarding", fresh.OnboardingID, "UsersOnboarding object ("+fresh.OnboardingID+")", changes, audit,
		nil, "", now); err != nil {
		return err
	}
	// config.save(): every field written again; only these differ.
	if _, err = tx.ExecContext(ctx, `UPDATE instance_instanceconfiguration SET primary_association_id=$2, updated_at=$3 WHERE id=$1`,
		id, fresh.AssociationID, now()); err != nil {
		return err
	}
	return tx.Commit()
}

func (r *InstanceSetup) audit(ctx context.Context, tx *sqlx.Tx, model, key, representation string, changes map[string][2]string,
	audit domain.LoginAuditContext, association *string, path string, now func() time.Time) error {
	entry := modelAuditEntry{Model: model, Key: key, Representation: representation, Action: 0, Changes: changes, Context: audit, At: now()}
	if association != nil {
		entry.AssociationID, entry.ResolutionPath = association, &path
	}
	return appendModelAudit(ctx, tx, entry)
}

// Reconfigure is instance.save(update_fields=[...the edited fields...,
// 'updated_at']) on the configuration row read by the view.
func (r *InstanceSetup) Reconfigure(ctx context.Context, id int64, columns map[string]any, display *string, at time.Time) error {
	names := make([]string, 0, len(columns))
	for name := range columns {
		names = append(names, name)
	}
	sort.Strings(names)
	sets, args := "updated_at=$1", []any{at}
	for _, name := range names {
		args = append(args, columns[name])
		sets += "," + name + "=$" + strconv.Itoa(len(args))
	}
	if display != nil {
		args = append(args, *display)
		sets += ",display_settings=$" + strconv.Itoa(len(args)) + "::jsonb"
	}
	args = append(args, id)
	_, err := r.db.ExecContext(ctx, `UPDATE instance_instanceconfiguration SET `+sets+` WHERE id=$`+strconv.Itoa(len(args)), args...)
	return err
}

// SetLogo is config.save(update_fields=['logo_path', 'updated_at']).
func (r *InstanceSetup) SetLogo(ctx context.Context, id int64, path string, at time.Time) error {
	_, err := r.db.ExecContext(ctx, `UPDATE instance_instanceconfiguration SET logo_path=$2, updated_at=$3 WHERE id=$1`, id, path, at)
	return err
}
