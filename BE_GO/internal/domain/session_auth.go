package domain

import (
	"context"
	"errors"
)

var ErrInvalidCachedSession = errors.New("invalid cached Django session")

// SessionActor is only the pre-view audit actor. It conveys no API role, scope,
// Bearer authentication or impersonation authority.
type SessionActor struct{ ID, Email string }
type SessionIdentity struct {
	Actor *SessionActor
	Empty bool
}
type SessionUser struct {
	ID       string `db:"user_id"`
	Email    string `db:"email"`
	Password string `db:"password" json:"-"`
	Active   bool   `db:"is_active"`
}
type SessionUsers interface {
	SessionUser(context.Context, string) (SessionUser, error)
}
type DjangoSessions interface {
	LoadSession(context.Context, string) (any, error)
	DeleteSession(context.Context, string) error
}
