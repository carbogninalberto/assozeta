package service

import (
	"context"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type PasswordSelector interface {
	Select(context.Context, any, any) (PasswordSelection, error)
}
type LoginPreparer interface {
	Prepare(context.Context, string, domain.LoginAuditContext) (domain.LoginChallenge, error)
}
type LoginSecondFactor interface {
	Check(context.Context, bool, *string, any, bool) error
}
type PasswordAdmission struct {
	passwords    PasswordSelector
	preparation  LoginPreparer
	secondFactor LoginSecondFactor
}

func NewPasswordAdmission(passwords PasswordSelector, preparation LoginPreparer, secondFactor LoginSecondFactor) *PasswordAdmission {
	return &PasswordAdmission{passwords: passwords, preparation: preparation, secondFactor: secondFactor}
}

// Admit composes password selection, persisted normalization/audit and OTP. The
// caller must still build the response and commit final login side effects before
// issuing a session. Audit metadata identifies the request actor, not this account.
func (s *PasswordAdmission) Admit(ctx context.Context, identifier, password any, otp any, present bool, audit domain.LoginAuditContext) (*domain.Account, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	selected, err := s.passwords.Select(ctx, identifier, password)
	if err != nil {
		return nil, err
	}
	switch selected.Failure {
	case "not_found":
		return nil, ErrLoginNotFound
	case "invalid":
		return nil, ErrLoginCredentials
	}
	if selected.Account == nil {
		return nil, errors.New("credential selection returned no account")
	}
	challenge, err := s.preparation.Prepare(ctx, selected.Account.ID, audit)
	if err != nil {
		return nil, err
	}
	if challenge.ID != selected.Account.ID {
		return nil, errors.New("login preparation returned another account")
	}
	if err = s.secondFactor.Check(ctx, challenge.TwoFactorEnabled, challenge.TwoFactorSecret, otp, present); err != nil {
		return nil, err
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	challenge.Password = ""
	return &challenge.Account, nil
}
