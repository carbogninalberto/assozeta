package service

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// RetiredSoftwareBilling only selects the legacy endpoint tombstone. It has no
// entitlement, payment or provider dependencies and is not a business access gate.
type RetiredSoftwareBilling struct{}

func NewRetiredSoftwareBilling() *RetiredSoftwareBilling { return &RetiredSoftwareBilling{} }

func (*RetiredSoftwareBilling) AssociationTombstone(ctx context.Context, identity domain.ResolvedIdentity) (bool, error) {
	if err := ctx.Err(); err != nil {
		return false, err
	}
	if identity.Actor == nil || identity.Effective == nil || identity.ScopeUser == nil {
		return false, domain.ErrInvalidScopedRequest
	}
	return identity.ScopeUser.Role == domain.RoleAssociation, nil
}
