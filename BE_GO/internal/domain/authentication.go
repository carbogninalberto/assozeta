package domain

import "errors"

// UUIDField rejects an explicitly malformed signed identity through an uncaught
// Django validation failure; this differs from a token missing its user_id claim.
var ErrMalformedAccountClaim = errors.New("malformed account identity claim")

// AuthenticationFailure describes credential admission, before identity scoping.
// HTTP rendering (including SimpleJWT token-message details) belongs to handlers.
type AuthenticationFailure struct {
	Code, Detail string
	TokenExpired bool
}

func (e *AuthenticationFailure) Error() string { return e.Detail }
