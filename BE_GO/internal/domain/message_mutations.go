package domain

import (
	"context"
	"errors"
	"time"
)

var (
	ErrMessageMutationForbidden = errors.New("message mutation forbidden")
	ErrMessageMutationNotFound  = errors.New("message mutation not found")
	ErrMessageMutationInvalid   = errors.New("message not valid")
)

// MessageMutationState retains database nullability for legacy DELETE audits.
type MessageMutationState struct {
	ID            string     `db:"message_id"`
	AssociationID string     `db:"sport_association_id"`
	Type          string     `db:"type"`
	Message       string     `db:"message"`
	Subject       *string    `db:"subject"`
	CreatedAt     *time.Time `db:"created_at"`
}

type MessageMutationAudit struct {
	ID                            string
	Action                        int
	Changes                       map[string][2]string
	Context                       LoginAuditContext
	AssociationID, ResolutionPath string
	At                            time.Time
}

type MessageMutationStore interface {
	BeginMessageMutations(context.Context) (MessageMutationTransaction, error)
}

type MessageMutationTransaction interface {
	OwnerForUpdate(context.Context, string) (int, bool, bool, error)
	AssociationForOwner(context.Context, string, *string) (string, error)
	MessageForUpdate(context.Context, string, string) (MessageMutationState, error)
	InsertMessage(context.Context, MessageMutationState) error
	DeleteMessageDeliveries(context.Context, string) error
	DeleteMessage(context.Context, string, string) error
	AppendMessageAudit(context.Context, MessageMutationAudit) error
	Commit() error
	Rollback() error
}
