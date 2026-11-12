package domain

import (
	"context"
	"errors"
)

var ErrCredentialConflict = errors.New("concurrent credential transaction conflict")

// PasswordLoginTransaction is the credential stage only. It must never be used
// as proof that OTP, account normalization or endpoint authorization succeeded.
type PasswordLoginTransaction interface {
	Candidates(context.Context, bool, string, string) ([]Account, error)
	ReplacePasswords(context.Context, []PasswordReplacement) error
	Commit() error
	Rollback() error
}
type PasswordLoginStore interface {
	BeginPasswordLogin(context.Context) (PasswordLoginTransaction, error)
}
type PasswordReplacement struct{ ID, Encoded string }

const PasswordCandidatePage = 128
