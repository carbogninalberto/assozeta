package domain

import (
	"context"
	"errors"
)

// ErrCollaboratorNotFound is ValidationError('Collaborator not found') (400).
var ErrCollaboratorNotFound = errors.New("Collaborator not found")

// CollaboratorUpdateTarget is the locked live user connected to the owner.
type CollaboratorUpdateTarget struct {
	FirstName   string  `db:"first_name"`
	LastName    string  `db:"last_name"`
	Role        int64   `db:"collaborator_role"`
	Permissions *string `db:"collaborator_permissions"` // jsonb text, nil for SQL NULL
}

type CollaboratorUpdateStore interface {
	BeginCollaboratorUpdate(context.Context) (CollaboratorUpdateTransaction, error)
}

type CollaboratorUpdateTransaction interface {
	// ConnectedCollaboratorForUpdate is User.objects.filter(connected_user=owner,
	// user_id=id).first(); nil when no live user matches.
	ConnectedCollaboratorForUpdate(ctx context.Context, owner, id string) (*CollaboratorUpdateTarget, error)
	// ActiveAssociationOf is SportAssociation.objects.filter(user=...).first().
	ActiveAssociationOf(ctx context.Context, user string) (*string, error)
	AppendCollaboratorAudit(context.Context, DashboardLayoutAudit) error
	// SetCollaboratorRole runs in a savepoint; a database rejection is a
	// *DashboardLayoutRejected. nil permissions store SQL NULL.
	SetCollaboratorRole(ctx context.Context, user string, role int64, permissions *string) error
	Commit() error
	Rollback() error
}
