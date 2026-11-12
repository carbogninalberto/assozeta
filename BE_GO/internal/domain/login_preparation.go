package domain

import (
	"context"
	"encoding/json"
	"time"
)

// AuditImpersonation preserves the original actor separately from the selected
// effective person and association scope on protected request mutations.
type AuditImpersonation struct {
	SessionID     string  `json:"impersonation_session"`
	ActorID       string  `json:"actor_id"`
	TargetID      string  `json:"target_user_id"`
	AssociationID *string `json:"association_id"`
}

// LoginAuditContext comes from trusted request/session admission. Its actor is
// independent of the account being logged into, and must not come from a body ID.
type LoginAuditContext struct {
	AdditionalData *AuditImpersonation `json:"additional_data,omitempty"`
	ActorID        *string             `json:"actor_id"`
	ActorEmail     *string             `json:"actor_email"`
	RemoteAddress  *string             `json:"remote_address"`
	RemotePort     *json.Number        `json:"remote_port"`
	CorrelationID  *string             `json:"correlation_id"`
}
type LoginChallenge struct {
	Account
	FirstName        string  `db:"first_name"`
	LastName         string  `db:"last_name"`
	TwoFactorEnabled bool    `db:"two_fa"`
	TwoFactorSecret  *string `db:"two_fa_secret" json:"-"`
}
type UsernameAudit struct {
	AccountID, ObjectRepresentation, Before, After string
	Context                                        LoginAuditContext
	AssociationID                                  *string
	ResolutionPath                                 *string
	At                                             time.Time
}
type LoginPreparationStore interface {
	BeginLoginPreparation(context.Context) (LoginPreparationTransaction, error)
}
type LoginPreparationTransaction interface {
	AccountForUpdate(context.Context, string) (LoginChallenge, error)
	SetUsername(context.Context, string, string) error
	AuditAssociations(context.Context, string, *string) (map[string]string, error)
	AppendUsernameAudit(context.Context, UsernameAudit) error
	Commit() error
	Rollback() error
}
