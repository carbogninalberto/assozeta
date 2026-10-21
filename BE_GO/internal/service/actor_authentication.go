package service

import (
	"context"
	"errors"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

type ActorAccounts interface {
	ByID(context.Context, string) (domain.Account, error)
}
type ActorTokens interface {
	Verify(string, string) (map[string]any, error)
}

type ActorAuthentication struct {
	accounts ActorAccounts
	tokens   ActorTokens
}

func NewActorAuthentication(accounts ActorAccounts, tokens ActorTokens) *ActorAuthentication {
	return &ActorAuthentication{accounts: accounts, tokens: tokens}
}

// Authenticate performs only the JWTAuthentication stage used by Django's
// ScopedJWTAuthentication. nil,nil means no applicable Bearer credential, never
// an authenticated anonymous account. A nonnil actor MUST still pass trusted
// identity/impersonation resolution and endpoint/resource authorization.
func (s *ActorAuthentication) Authenticate(ctx context.Context, authorization string) (*domain.Account, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	parts := strings.FieldsFunc(authorization, func(r rune) bool { return r == ' ' || r == '\t' || r == '\n' || r == '\r' || r == '\v' || r == '\f' })
	// SimpleJWT's configured Bearer scheme comparison is case-sensitive.
	if len(parts) == 0 || parts[0] != "Bearer" {
		return nil, nil
	}
	if len(parts) != 2 {
		return nil, &domain.AuthenticationFailure{Code: "bad_authorization_header", Detail: "Authorization header must contain two space-delimited values"}
	}
	claims, err := s.tokens.Verify(parts[1], "access")
	if err != nil {
		return nil, &domain.AuthenticationFailure{Code: "token_not_valid", Detail: "Given token not valid for any token type"}
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	value, present := claims["user_id"]
	if present && value == nil {
		// Django turns an explicit null UUID filter into IS NULL; a primary key can
		// never match it. Missing user_id is a different SimpleJWT error.
		return nil, &domain.AuthenticationFailure{Code: "user_not_found", Detail: "User not found"}
	}
	id, ok := claimAccountID(value)
	if !present {
		return nil, &domain.AuthenticationFailure{Code: "token_not_valid", Detail: "Token contained no recognizable user identification"}
	}
	if !ok {
		return nil, domain.ErrMalformedAccountClaim
	}
	// Ignore token role, email and superuser assertions. Current persisted state
	// must be read on every request so disabling/deleting an account revokes access.
	actor, err := s.accounts.ByID(ctx, id.String())
	if errors.Is(err, domain.ErrAccountNotFound) {
		return nil, &domain.AuthenticationFailure{Code: "user_not_found", Detail: "User not found"}
	}
	if err != nil {
		return nil, err
	}
	if !actor.Active {
		return nil, &domain.AuthenticationFailure{Code: "user_inactive", Detail: "User is inactive"}
	}
	actor.Password = "" // The resolved actor never carries a credential hash onward.
	return &actor, nil
}

// UUIDField also accepts integer JSON claims. JSONNumber avoids loss of the
// 128-bit identity through float64; bool follows Python's integer subtype.
func claimAccountID(value any) (uuid.UUID, bool) {
	id, _, err := referenceUUID(value)
	if err != nil {
		return uuid.Nil, false
	}
	parsed, err := uuid.Parse(id)
	return parsed, err == nil
}
