package service

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// memberDocumentAccess is docmanager.access.require_document_access. Documents
// are served to the association's staff, the instance owner and administrators,
// and to the member they belong to: an athlete account that is neither a
// superuser nor the instance owner needs the record among its own (reaches).
// Association-scoped sessions keep their own boundary and requests admitted
// without an account are decided by the view's permission classes.
func memberDocumentAccess(ctx context.Context, store domain.MemberDocumentStore, identity domain.ResolvedIdentity, user *domain.Account,
	reaches func(ctx context.Context, member string) (bool, error)) error {
	if identity.ImpersonationAssociationID != "" || user == nil {
		return nil
	}
	if user.Role != domain.RoleAthlete || user.Superuser {
		return nil
	}
	owner, err := store.InstanceOwner(ctx, user.ID)
	if err != nil || owner {
		return err
	}
	own, err := reaches(ctx, user.ID)
	if err != nil {
		return err
	}
	if !own {
		return &domain.PermissionDenied{Detail: unavailableResource}
	}
	return nil
}
