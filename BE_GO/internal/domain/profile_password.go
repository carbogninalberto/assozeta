package domain

import "context"

// Only credential state is locked and changed; passwords are excluded from
// Django's audit registration. No login/session or unrelated profile writes.
type ProfilePasswordTransaction interface {
	PasswordForUpdate(context.Context, string) (string, error)
	SetPassword(context.Context, string, string) error
	Commit() error
	Rollback() error
}
type ProfilePasswordStore interface {
	BeginProfilePassword(context.Context) (ProfilePasswordTransaction, error)
}
