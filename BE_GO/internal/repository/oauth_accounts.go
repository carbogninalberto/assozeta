package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

// OAuthAccounts stores the oauth2/signup, oauth2/partial-signup, oauth2/reset
// and oauth2/delete-account writes. Every row write commits with its auditlog
// entry and index row (the views save without a transaction, one row at a time).
type OAuthAccounts struct{ db *sqlx.DB }

func NewOAuthAccounts(db *sqlx.DB) *OAuthAccounts { return &OAuthAccounts{db: db} }

var _ domain.OAuthAccountsStore = (*OAuthAccounts)(nil)

// InstanceConfig is InstanceConfiguration.objects.first() for instance_email_brand.
func (r *OAuthAccounts) InstanceConfig(ctx context.Context) (domain.EmailRenderInstanceConfig, error) {
	return NewEmailRenderRoutes(r.db).InstanceConfig(ctx)
}

// Django's iexact on PostgreSQL: UPPER(column::text) = UPPER(value).
const oauthPartialByEmail = `SELECT id FROM bakney_user_partial WHERE UPPER(email::text)=UPPER($1) ORDER BY id LIMIT 1`

func (r *OAuthAccounts) PartialExists(ctx context.Context, email string) (bool, error) {
	var id int64
	err := r.db.GetContext(ctx, &id, oauthPartialByEmail, email)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil
	}
	return err == nil, err
}

func (r *OAuthAccounts) begin(ctx context.Context) (*ownedTransaction, error) {
	return beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
}

// actorIndex is the audit index fallback: SportAssociation.objects.filter(user=actor).first().
func actorIndex(ctx context.Context, tx *sqlx.Tx, audit domain.LoginAuditContext, entry *modelAuditEntry) error {
	if audit.ActorID == nil {
		return nil
	}
	return userIndex(ctx, tx, *audit.ActorID, "actor.sport_association", entry)
}

func userIndex(ctx context.Context, tx *sqlx.Tx, user, path string, entry *modelAuditEntry) error {
	var association string
	err := tx.GetContext(ctx, &association, `SELECT sport_association_id::text FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted ORDER BY sport_association_id LIMIT 1`, user)
	if errors.Is(err, sql.ErrNoRows) {
		return nil
	}
	if err != nil {
		return err
	}
	entry.AssociationID, entry.ResolutionPath = &association, &path
	return nil
}

// CreatePartial is UserPartial.objects.create(email=...) with its create entry
// (the model has no association: the index follows the actor).
func (r *OAuthAccounts) CreatePartial(ctx context.Context, email string, at time.Time, audit domain.LoginAuditContext) error {
	tx, err := r.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var id int64
	if err = tx.tx.GetContext(ctx, &id, `INSERT INTO bakney_user_partial (email,creation_date) VALUES($1,$2) RETURNING id`, email, at); err != nil {
		return err
	}
	key := strconv.FormatInt(id, 10)
	changes, err := createdModelChanges(ctx, tx.tx, "bakney_user_partial", "id", key)
	if err != nil {
		return err
	}
	entry := modelAuditEntry{Model: "userpartial", Key: key, Representation: "UserPartial object (" + key + ")", Action: 0,
		Changes: changes, Context: audit, At: at, ObjectID: &id}
	if err = actorIndex(ctx, tx.tx, audit, &entry); err != nil {
		return err
	}
	if err = appendModelAudit(ctx, tx.tx, entry); err != nil {
		return err
	}
	return tx.Commit()
}

// DeletePartial is UserPartial.objects.filter(email__iexact=...).first().delete().
func (r *OAuthAccounts) DeletePartial(ctx context.Context, email string, at time.Time, audit domain.LoginAuditContext) error {
	tx, err := r.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var id int64
	err = tx.tx.GetContext(ctx, &id, oauthPartialByEmail+` FOR UPDATE`, email)
	if errors.Is(err, sql.ErrNoRows) {
		return nil
	}
	if err != nil {
		return err
	}
	key := strconv.FormatInt(id, 10)
	changes, _, err := deleteModelWithAudit(ctx, tx.tx, "bakney_user_partial", "id", key)
	if err != nil {
		return err
	}
	entry := modelAuditEntry{Model: "userpartial", Key: key, Representation: "UserPartial object (" + key + ")", Action: 2,
		Changes: changes, Context: audit, At: at, ObjectID: &id}
	if err = actorIndex(ctx, tx.tx, audit, &entry); err != nil {
		return err
	}
	if err = appendModelAudit(ctx, tx.tx, entry); err != nil {
		return err
	}
	return tx.Commit()
}

const oauthResetColumns = `SELECT user_id::text AS user_id,first_name,last_name,username,email FROM bakney_user`

// ResetUsers is User.objects.filter(email__iexact=email).order_by('user_id').
func (r *OAuthAccounts) ResetUsers(ctx context.Context, email string) ([]domain.OAuthResetUser, error) {
	rows := []domain.OAuthResetUser{}
	err := r.db.SelectContext(ctx, &rows, oauthResetColumns+` WHERE NOT deleted AND UPPER(email::text)=UPPER($1) ORDER BY user_id`, email)
	return rows, err
}

// ResetUser is User.objects.get(user_id=...) (a live user); nil when missing.
func (r *OAuthAccounts) ResetUser(ctx context.Context, id string) (*domain.OAuthResetUser, error) {
	var row domain.OAuthResetUser
	err := r.db.GetContext(ctx, &row, oauthResetColumns+` WHERE NOT deleted AND user_id=$1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// SetPassword is set_password + save(): auditlog excludes the password, so
// the unchanged fields produce no entry.
func (r *OAuthAccounts) SetPassword(ctx context.Context, id, hash string) error {
	result, err := r.db.ExecContext(ctx, `UPDATE bakney_user SET password=$2 WHERE user_id=$1`, id, hash)
	if err != nil {
		return err
	}
	if n, err := result.RowsAffected(); err == nil && n != 1 {
		return domain.ErrAccountNotFound
	}
	return err
}

// ScheduleDeletion is user.delete_on = date; user.save(): an update entry only
// when the date changes, indexed through the user's association, else the actor's.
func (r *OAuthAccounts) ScheduleDeletion(ctx context.Context, id string, date, at time.Time, audit domain.LoginAuditContext) (domain.OAuthDeletion, error) {
	tx, err := r.begin(ctx)
	if err != nil {
		return domain.OAuthDeletion{}, err
	}
	defer tx.Rollback()
	var state struct {
		FirstName string     `db:"first_name"`
		LastName  string     `db:"last_name"`
		Email     string     `db:"email"`
		Deleted   bool       `db:"deleted"`
		DeleteOn  *time.Time `db:"delete_on"`
	}
	err = tx.tx.GetContext(ctx, &state, `SELECT first_name,last_name,email,deleted,delete_on FROM bakney_user WHERE user_id=$1 FOR UPDATE`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.OAuthDeletion{}, domain.ErrAccountNotFound
	}
	if err != nil {
		return domain.OAuthDeletion{}, err
	}
	next := date.Format(time.DateOnly)
	before := "None"
	if state.DeleteOn != nil {
		before = state.DeleteOn.Format(time.DateOnly)
	}
	if before != next {
		if _, err = tx.tx.ExecContext(ctx, `UPDATE bakney_user SET delete_on=$2 WHERE user_id=$1`, id, next); err != nil {
			return domain.OAuthDeletion{}, err
		}
		entry := modelAuditEntry{Model: "user", Key: id, Representation: "name: " + state.FirstName + " " + state.LastName, Action: 1,
			Changes: map[string][2]string{"delete_on": {before, next}}, Context: audit, At: at}
		// The resolver reads the user through User.objects (live users only).
		if !state.Deleted {
			if err = userIndex(ctx, tx.tx, id, "user.sport_association", &entry); err != nil {
				return domain.OAuthDeletion{}, err
			}
		}
		if entry.AssociationID == nil {
			if err = actorIndex(ctx, tx.tx, audit, &entry); err != nil {
				return domain.OAuthDeletion{}, err
			}
		}
		if err = appendModelAudit(ctx, tx.tx, entry); err != nil {
			return domain.OAuthDeletion{}, err
		}
	}
	if err = tx.Commit(); err != nil {
		return domain.OAuthDeletion{}, err
	}
	return domain.OAuthDeletion{Email: state.Email, DeleteOn: date}, nil
}

// UsernameTaken is the UniqueValidator query (the default manager, exact match).
func (r *OAuthAccounts) UsernameTaken(ctx context.Context, username string) (bool, error) {
	var taken bool
	err := r.db.GetContext(ctx, &taken, `SELECT EXISTS(SELECT 1 FROM bakney_user WHERE username=$1)`, username)
	return taken, err
}

// Invite is CollaborationInvites.objects.filter(token=...).first(); a nil token is IS NULL.
func (r *OAuthAccounts) Invite(ctx context.Context, token *string) (*domain.OAuthInvite, error) {
	var row domain.OAuthInvite
	err := r.db.GetContext(ctx, &row, `SELECT collaboration_invite_id::text AS collaboration_invite_id,user_id::text AS user_id,
 expiration_date,collaborator_role,collaborator_permissions::text AS collaborator_permissions FROM application_collaborationinvites
 WHERE ($1::text IS NULL AND token IS NULL) OR token=$1 ORDER BY collaboration_invite_id LIMIT 1`, token)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// DeleteInvite is collaboration_invite.delete() with its delete entry.
func (r *OAuthAccounts) DeleteInvite(ctx context.Context, id string, at time.Time, audit domain.LoginAuditContext) error {
	tx, err := r.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	changes, _, err := deleteModelWithAudit(ctx, tx.tx, "application_collaborationinvites", "collaboration_invite_id", id)
	if err != nil {
		return err
	}
	entry := modelAuditEntry{Model: "collaborationinvites", Key: id, Representation: "CollaborationInvites object (" + id + ")",
		Action: 2, Changes: changes, Context: audit, At: at}
	if err = actorIndex(ctx, tx.tx, audit, &entry); err != nil {
		return err
	}
	if err = appendModelAudit(ctx, tx.tx, entry); err != nil {
		return err
	}
	return tx.Commit()
}

// CreateUser is User.objects.create_user(...) with every other field at its
// model default, and its create entry (the new user has no association: the
// index follows the actor).
func (r *OAuthAccounts) CreateUser(ctx context.Context, user domain.OAuthNewUser, audit domain.LoginAuditContext) error {
	tx, err := r.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var permissions any
	if user.Permissions != nil {
		permissions = string(user.Permissions)
	}
	_, err = tx.tx.ExecContext(ctx, `INSERT INTO bakney_user
 (password,last_login,is_superuser,username,first_name,last_name,email,is_staff,is_active,date_joined,user_id,role,two_fa,
  stripe_on_boarding_completed,enumerate_invoices,online_payments,balance_sheet_year,balance_sheet_start_day,balance_sheet_start_month,
  temporary_invoice_deletion,auto_archive,auto_mark_attendance,full_installments_plan,payment_date_equal_invoice_date,
  starting_number_invoices,auto_paid_payment,tables_settings,deleted,collaborator_role,dark_mode,integration_google_auto_sync,
  subscription_duration_equal_sport_year,subscription_duration,membership_duration,subscription_start_day,subscription_start_month,
  custom_end_date,subscription_end_day,subscription_end_month,membership_starting_number,default_membership_type,
  disable_account_creation,force_account_creation,medical_certificate_notifications,hide_category_name,show_zero_payments,
  imported_from_associami,connected_user_id,collaborator_permissions)
 VALUES ($1,NULL,FALSE,$2,$3,$4,$5,FALSE,TRUE,$6,$7,$8,FALSE,FALSE,FALSE,TRUE,1,1,1,TRUE,FALSE,FALSE,FALSE,FALSE,0,FALSE,'{}'::jsonb,
  FALSE,$9,FALSE,FALSE,TRUE,2,2,1,1,FALSE,31,12,0,'',FALSE,FALSE,TRUE,FALSE,FALSE,FALSE,$10,$11::jsonb)`,
		user.PasswordHash, user.Username, user.FirstName, user.LastName, user.Email, user.Joined, user.ID, user.Role, user.CollaboratorRole,
		user.ConnectedUserID, permissions)
	var failure *pgconn.PgError
	if errors.As(err, &failure) && failure.Code == "23505" {
		return domain.ErrOAuthUsernameConflict
	}
	if err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, tx.tx, "bakney_user", "user_id", user.ID)
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
	entry := modelAuditEntry{Model: "user", Key: user.ID, Representation: "name: " + user.FirstName + " " + user.LastName, Action: 0,
		Changes: changes, Context: audit, At: user.Joined}
	if err = actorIndex(ctx, tx.tx, audit, &entry); err != nil {
		return err
	}
	if err = appendModelAudit(ctx, tx.tx, entry); err != nil {
		return err
	}
	return tx.Commit()
}

// OAuthResetTokens is the reset view's redis.Redis(..., db=2).
type OAuthResetTokens struct{ client *redis.Client }

func NewOAuthResetTokens(client *redis.Client) *OAuthResetTokens {
	return &OAuthResetTokens{client: client}
}

var _ domain.OAuthResetTokens = (*OAuthResetTokens)(nil)

func (t *OAuthResetTokens) Set(ctx context.Context, token, userID string, ttl time.Duration) error {
	return t.client.Set(ctx, token, userID, ttl).Err()
}

func (t *OAuthResetTokens) Get(ctx context.Context, token string) (*string, error) {
	value, err := t.client.Get(ctx, token).Result()
	if errors.Is(err, redis.Nil) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &value, nil
}

func (t *OAuthResetTokens) Delete(ctx context.Context, token string) error {
	return t.client.Del(ctx, token).Err()
}
