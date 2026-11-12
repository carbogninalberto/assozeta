package service

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type TwoFactorInfo struct{}

func NewTwoFactorInfo() *TwoFactorInfo { return &TwoFactorInfo{} }

// Status receives the result of authentication and endpoint-specific identity
// admission. Django reads request.user, which is the tenant owner for a permitted
// collaborator, rather than the initiating actor or pre-scope effective person.
// Read the admitted account snapshot without another query or credential access.
func (*TwoFactorInfo) Status(ctx context.Context, identity domain.ResolvedIdentity) (bool, error) {
	if err := ctx.Err(); err != nil {
		return false, err
	}
	if identity.Actor == nil || identity.Effective == nil || identity.ScopeUser == nil {
		return false, domain.ErrInvalidScopedRequest
	}
	return identity.ScopeUser.TwoFactor, nil
}
