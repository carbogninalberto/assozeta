package service

import (
	"context"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// WebSocketIdentities is notifications.middleware.JWTAuthMiddleware: the
// token authentication and impersonation selection every WebSocket path gets.
type WebSocketIdentities struct {
	accounts ActorAccounts
	tokens   ActorTokens
	resolver *IdentityResolver
}

func NewWebSocketIdentities(accounts ActorAccounts, tokens ActorTokens, resolver *IdentityResolver) *WebSocketIdentities {
	return &WebSocketIdentities{accounts: accounts, tokens: tokens, resolver: resolver}
}

// Authenticate is _authenticate_token: a valid access token whose iss and aud
// claims equal the issued values and whose truthy user_id names an active,
// non-deleted user. Every failure is the anonymous user (nil).
func (s *WebSocketIdentities) Authenticate(ctx context.Context, token string) *domain.Account {
	claims, err := s.tokens.Verify(token, "access")
	if err != nil {
		return nil
	}
	if issuer, ok := claims["iss"].(string); !ok || issuer != "https://bakney.com" {
		return nil
	}
	if audience, ok := claims["aud"].(string); !ok || audience != "bakney" {
		return nil
	}
	value, present := claims["user_id"]
	if !present || !dto.PythonTruthy(value) {
		return nil
	}
	id, ok := claimAccountID(value)
	if !ok {
		return nil
	}
	account, err := s.accounts.ByID(ctx, id.String())
	if err != nil || !account.Active {
		return nil
	}
	account.Password = ""
	return &account
}

// WebSocketTarget is the user an impersonation session selects and the
// association the session is bound to ("" for an administrator's session).
type WebSocketTarget struct {
	User        *domain.Account
	Association string
}

// Impersonate is the middleware's selected_user(): the actor afresh, then
// resolve_target. ok is false where Django closes with 4003 (missing actor or
// PermissionDenied); other failures are errors (the ASGI application raises).
func (s *WebSocketIdentities) Impersonate(ctx context.Context, actor *domain.Account, identifier, path string) (WebSocketTarget, bool, error) {
	if actor == nil {
		return WebSocketTarget{}, false, nil
	}
	target, association, err := s.resolver.resolveTarget(ctx, actor, identifier, "")
	var denied *domain.PermissionDenied
	if errors.As(err, &denied) {
		return WebSocketTarget{}, false, nil
	}
	if err != nil {
		return WebSocketTarget{}, false, err
	}
	if association != "" && path == "/ws/agent/" && (target.Role == domain.RoleAthlete ||
		(target.Role == domain.RoleCollaborator && target.CollaboratorRole != 1)) {
		return WebSocketTarget{}, false, nil
	}
	return WebSocketTarget{User: cleanIdentityAccount(target), Association: association}, true, nil
}
