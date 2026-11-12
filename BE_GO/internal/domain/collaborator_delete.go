package domain

import (
	"context"
	"errors"
)

// ErrCollaboratorDeleteNotSportAssociation is the view's
// PermissionDenied('User is not a sport association') (403).
var ErrCollaboratorDeleteNotSportAssociation = errors.New("User is not a sport association")

// CollaboratorDeleteStore opens collaborators_delete's atomic_audit_mutation:
// one transaction for the lookups, both hard deletes and their audit/index rows.
type CollaboratorDeleteStore interface {
	BeginCollaboratorDelete(ctx context.Context, audit LoginAuditContext, group *string) (CollaboratorDeleteTransaction, error)
}

// CollaboratorDeleteInvitation is the locked invitation the uid names.
type CollaboratorDeleteInvitation struct {
	Email    string
	Accepted bool
}

type CollaboratorDeleteTransaction interface {
	// LockOwner is User.objects.select_for_update().get(pk=request.user.pk).
	LockOwner(ctx context.Context, owner string) error
	// DeleteConnectedUser locks User.objects.filter(connected_user=owner,
	// user_id=id).first() and, when found, deletes it (Model.delete()).
	DeleteConnectedUser(ctx context.Context, owner, id string) (bool, error)
	// Invitation locks CollaborationInvites.objects.filter(user=owner,
	// collaboration_invite_id=id).first(); nil when none.
	Invitation(ctx context.Context, owner, id string) (*CollaboratorDeleteInvitation, error)
	// DeleteAccountByEmail locks User.objects.filter(email=email,
	// connected_user=owner).first() and deletes it when found.
	DeleteAccountByEmail(ctx context.Context, owner, email string) error
	// DeleteInvitation deletes the invitation Invitation locked.
	DeleteInvitation(ctx context.Context) error
	Commit() error
	Rollback() error
}
