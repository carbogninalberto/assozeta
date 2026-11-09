package domain

import (
	"context"
	"encoding/json"
	"errors"
	"time"
)

// ErrOAuthUsernameConflict is create_user's IntegrityError on bakney_user.username.
var ErrOAuthUsernameConflict = errors.New("username unique constraint")

// OAuthResetUser is what the password reset e-mails read from a user.
type OAuthResetUser struct {
	ID        string `db:"user_id"`
	FirstName string `db:"first_name"`
	LastName  string `db:"last_name"`
	Username  string `db:"username"`
	Email     string `db:"email"`
}

// OAuthInvite is the CollaborationInvites row oauth2_signup consumes.
type OAuthInvite struct {
	ID             string     `db:"collaboration_invite_id"`
	UserID         string     `db:"user_id"`
	ExpirationDate *time.Time `db:"expiration_date"`
	Role           int        `db:"collaborator_role"`
	Permissions    *string    `db:"collaborator_permissions"` // jsonb text; nil: SQL NULL
}

// OAuthNewUser is User.objects.create_user(...) as oauth2_signup calls it.
type OAuthNewUser struct {
	ID, Username, Email, PasswordHash string
	FirstName, LastName               string
	Role, CollaboratorRole            int
	ConnectedUserID                   *string
	Permissions                       json.RawMessage // nil: SQL NULL
	Joined                            time.Time
}

// OAuthDeletion is the outcome of oauth2_delete_account's save().
type OAuthDeletion struct {
	Email    string
	DeleteOn time.Time
}

// OAuthAccountsStore holds the writes of oauth2/signup, oauth2/partial-signup,
// oauth2/reset and oauth2/delete-account. Each write commits with its
// auditlog entry and index row, as the views' non-atomic saves do one by one.
type OAuthAccountsStore interface {
	PartialExists(ctx context.Context, email string) (bool, error)
	CreatePartial(ctx context.Context, email string, at time.Time, audit LoginAuditContext) error
	DeletePartial(ctx context.Context, email string, at time.Time, audit LoginAuditContext) error
	ResetUsers(ctx context.Context, email string) ([]OAuthResetUser, error)
	ResetUser(ctx context.Context, id string) (*OAuthResetUser, error)
	SetPassword(ctx context.Context, id, hash string) error
	ScheduleDeletion(ctx context.Context, id string, date time.Time, at time.Time, audit LoginAuditContext) (OAuthDeletion, error)
	UsernameTaken(ctx context.Context, username string) (bool, error)
	Invite(ctx context.Context, token *string) (*OAuthInvite, error)
	DeleteInvite(ctx context.Context, id string, at time.Time, audit LoginAuditContext) error
	CreateUser(ctx context.Context, user OAuthNewUser, audit LoginAuditContext) error
	InstanceConfig(ctx context.Context) (EmailRenderInstanceConfig, error)
}

// OAuthResetTokens is the reset view's own Redis client (DB 2).
type OAuthResetTokens interface {
	Set(ctx context.Context, token, userID string, ttl time.Duration) error
	Get(ctx context.Context, token string) (*string, error)
	Delete(ctx context.Context, token string) error
}
