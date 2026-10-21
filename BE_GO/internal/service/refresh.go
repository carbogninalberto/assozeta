package service

import (
	"context"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/pkg/jwt"
)

var ErrRefreshRequired = errors.New("refresh_token required")
var ErrRefreshInvalid = errors.New("invalid or expired refresh token")

type RefreshTokens interface {
	Refresh(string) (jwt.Pair, error)
	IssueUnbound() (jwt.Pair, error)
}
type RefreshService struct{ tokens RefreshTokens }

func NewRefreshService(tokens RefreshTokens) *RefreshService { return &RefreshService{tokens: tokens} }

// Refresh preserves JWTTokenService's credential-only semantics. Optional Bearer
// authentication/impersonation must already have run. The refresh token's account
// is not loaded or rebound to that actor, and no last-login/audit writes occur.
func (s *RefreshService) Refresh(ctx context.Context, value any, present bool) (jwt.Pair, error) {
	if err := ctx.Err(); err != nil {
		return jwt.Pair{}, err
	}
	if !present {
		return jwt.Pair{}, ErrRefreshRequired
	}
	var result jwt.Pair
	var err error
	if value == nil {
		// RefreshToken(None) constructs a new unbound token in current SimpleJWT.
		// It has no user identity and cannot authenticate a REST account.
		result, err = s.tokens.IssueUnbound()
	} else if token, ok := value.(string); ok {
		result, err = s.tokens.Refresh(token)
	} else {
		return jwt.Pair{}, ErrRefreshInvalid
	}
	if err := ctx.Err(); err != nil {
		return jwt.Pair{}, err
	}
	if err != nil {
		return jwt.Pair{}, ErrRefreshInvalid
	}
	return result, nil
}
