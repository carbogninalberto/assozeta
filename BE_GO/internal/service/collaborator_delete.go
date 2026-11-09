package service

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type CollaboratorDeletes struct {
	store domain.CollaboratorDeleteStore
}

func NewCollaboratorDeletes(store domain.CollaboratorDeleteStore) *CollaboratorDeletes {
	return &CollaboratorDeletes{store: store}
}

// Delete follows collaborators_delete after method dispatch: the sport
// association check on request.user (the owner for collaborators),
// is_valid_uuid, then inside one transaction the owner lock, the connected
// live user or else the owner's invitation (an accepted one first deletes the
// live account with its exact email, lowest key), and the hard delete.
// Deletes run Django's Collector (repository/collaborator_delete_collector.go);
// constraint failures surface at commit.
func (s *CollaboratorDeletes) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw, groupHeader string, audit domain.LoginAuditContext) error {
	if role := identity.ScopeUser.Role; role != domain.RoleAssociation && role != domain.RoleCollaborator {
		return domain.ErrCollaboratorDeleteNotSportAssociation
	}
	parsed, err := paymentInfoParseUUID(raw)
	if err != nil {
		return err
	}
	id, owner := parsed.String(), identity.ScopeUser.ID
	tx, err := s.store.BeginCollaboratorDelete(ctx, paymentCategoryAuditContext(identity, audit), supplierGroup(groupHeader))
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = tx.LockOwner(ctx, owner); err != nil {
		return err
	}
	found, err := tx.DeleteConnectedUser(ctx, owner, id)
	if err != nil {
		return err
	}
	if !found {
		invitation, err := tx.Invitation(ctx, owner, id)
		if err != nil {
			return err
		}
		if invitation == nil {
			return domain.ErrCollaboratorNotFound
		}
		if invitation.Accepted {
			if err = tx.DeleteAccountByEmail(ctx, owner, invitation.Email); err != nil {
				return err
			}
		}
		if err = tx.DeleteInvitation(ctx); err != nil {
			return err
		}
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}
