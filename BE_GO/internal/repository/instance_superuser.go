package repository

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jmoiron/sqlx"
)

// InstanceSuperusers backs the create_instance_superuser command: the
// iexact identity checks on User.original_objects and
// User.objects.create_superuser inside transaction.atomic (the row and its
// auditlog create entry; no index row is resolved for a user without an
// association and without an actor).
type InstanceSuperusers struct{ db *sqlx.DB }

func NewInstanceSuperusers(db *sqlx.DB) *InstanceSuperusers { return &InstanceSuperusers{db: db} }

// Exists is User.original_objects.filter(<field>__iexact=value).exists().
func (r *InstanceSuperusers) Exists(ctx context.Context, field, value string) (bool, error) {
	var statement string
	switch field {
	case "username":
		statement = `SELECT EXISTS(SELECT 1 FROM bakney_user WHERE UPPER(username::text)=UPPER($1))`
	case "email":
		statement = `SELECT EXISTS(SELECT 1 FROM bakney_user WHERE UPPER(email::text)=UPPER($1))`
	default:
		return false, errors.New("unsupported identity field")
	}
	var exists bool
	err := r.db.GetContext(ctx, &exists, statement, value)
	return exists, err
}

// Create inserts the superuser with the model defaults (role athlete, staff,
// superuser, active) and writes its auditlog create entry in one transaction.
// A unique violation is domain.ErrInstanceSuperuserIntegrity.
func (r *InstanceSuperusers) Create(ctx context.Context, user domain.InstanceSuperuser, now func() time.Time) error {
	owned, err := beginOwnedTransaction(ctx, r.db, nil)
	if err != nil {
		return err
	}
	defer owned.Rollback()
	tx := owned.tx
	if _, err = tx.ExecContext(ctx, `INSERT INTO bakney_user
 (password,last_login,is_superuser,username,first_name,last_name,email,is_staff,is_active,date_joined,user_id,role,phone,two_fa,
  stripe_on_boarding_completed,enumerate_invoices,online_payments,balance_sheet_year,balance_sheet_start_day,balance_sheet_start_month,
  temporary_invoice_deletion,auto_archive,auto_mark_attendance,full_installments_plan,payment_date_equal_invoice_date,
  starting_number_invoices,auto_paid_payment,tables_settings,deleted,collaborator_role,dark_mode,integration_google_auto_sync,
  subscription_duration_equal_sport_year,subscription_duration,membership_duration,subscription_start_day,subscription_start_month,
  custom_end_date,subscription_end_day,subscription_end_month,membership_starting_number,default_membership_type,
  disable_account_creation,force_account_creation,medical_certificate_notifications,hide_category_name,show_zero_payments,
  imported_from_associami)
 VALUES ($1,NULL,TRUE,$2,$3,$4,$5,TRUE,TRUE,$6,$7,2,$8,FALSE,FALSE,FALSE,TRUE,1,1,1,TRUE,FALSE,FALSE,FALSE,FALSE,0,FALSE,'{}'::jsonb,
  FALSE,1,FALSE,FALSE,TRUE,2,2,1,1,FALSE,31,12,0,'',FALSE,FALSE,TRUE,FALSE,FALSE,FALSE)`,
		user.PasswordHash, user.Username, user.FirstName, user.LastName, user.Email, now(), user.UserID, user.Phone); err != nil {
		var pg *pgconn.PgError
		if errors.As(err, &pg) && pg.Code == "23505" {
			return domain.ErrInstanceSuperuserIntegrity
		}
		return err
	}
	changes, err := createdModelChanges(ctx, tx, "bakney_user", "user_id", user.UserID)
	if err != nil {
		return err
	}
	// auditlog.register(User, exclude_fields=['last_login', 'password']);
	// reverse foreign keys with a related_name render as their managers.
	delete(changes, "password")
	delete(changes, "last_login")
	for name, manager := range map[string]string{"requester": "application.SubscriptionTransfer.None", "recipient": "application.SubscriptionTransfer.None",
		"saved_reports": "application.SavedReport.None", "social_auth": "social_django.UserSocialAuth.None"} {
		changes[name] = [2]string{"None", manager}
	}
	if err = appendModelAudit(ctx, tx, modelAuditEntry{Model: "user", Key: user.UserID,
		Representation: "name: " + user.FirstName + " " + user.LastName, Action: 0, Changes: changes, At: now()}); err != nil {
		return err
	}
	return owned.Commit()
}
