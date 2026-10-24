package domain

import (
	"context"
	"time"

	"github.com/google/uuid"
)

// The user projection omits password and private account fields.
type CollaboratorRecord struct {
	ID                         uuid.UUID
	FirstName, LastName, Email string
	Phone                      *string
	Role, CollaboratorRole     int
	ConnectedUser              *uuid.UUID
	Permissions                []byte
}
type CollaborationInviteRecord struct {
	ID               uuid.UUID
	Email            string
	Expires          *time.Time
	Accepted         bool
	Token            *string
	CollaboratorRole int
	Permissions      []byte
}
type CollaboratorReader interface {
	Collaborators(context.Context, uuid.UUID) ([]CollaboratorRecord, error)
	Collaborator(context.Context, uuid.UUID, uuid.UUID) (*CollaboratorRecord, error)
	CollaborationInvites(context.Context, uuid.UUID) ([]CollaborationInviteRecord, error)
}
