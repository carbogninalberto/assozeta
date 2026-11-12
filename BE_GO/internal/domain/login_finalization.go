package domain

import (
	"context"
	"time"
)

// Read under a row lock after credentials, OTP and response construction.
// Credentials and unrelated mutable user fields are never rewritten here.
type LoginFinalizationState struct {
	ID        string     `db:"user_id"`
	FirstName string     `db:"first_name"`
	LastName  string     `db:"last_name"`
	DeleteOn  *time.Time `db:"delete_on"`
}
type LoginDeletionAudit struct {
	AccountID, ObjectRepresentation, Before string
	Context                                 LoginAuditContext
	AssociationID, ResolutionPath           *string
	At                                      time.Time
}
type LoginFinalizationStore interface {
	BeginLoginFinalization(context.Context) (LoginFinalizationTransaction, error)
}
type LoginFinalizationTransaction interface {
	FinalizationForUpdate(context.Context, string) (LoginFinalizationState, error)
	SetLoginTime(context.Context, string, time.Time, bool) error
	AuditAssociations(context.Context, string, *string) (map[string]string, error)
	AppendLoginDeletionAudit(context.Context, LoginDeletionAudit) error
	Commit() error
	Rollback() error
}
