package service

import (
	"context"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/jwt"
)

type PasswordSessionAdmission interface {
	Admit(context.Context, any, any, any, bool, domain.LoginAuditContext) (*domain.Account, error)
}
type LoginTokenIssuer interface {
	Issue(string, int, string) (jwt.Pair, error)
}
type LoginResponseBuilder interface {
	Build(context.Context, domain.Account, jwt.Pair, domain.LoginAuditContext) (*dto.LoginResponse, error)
}
type LoginFinalizer interface {
	Finalize(context.Context, string, domain.LoginAuditContext) error
}

var ErrLoginSessionInvariant = errors.New("login session dependency returned inconsistent identity or response")

type PasswordSessions struct {
	admission    PasswordSessionAdmission
	tokens       LoginTokenIssuer
	responses    LoginResponseBuilder
	finalization LoginFinalizer
}

func NewPasswordSessions(admission PasswordSessionAdmission, tokens LoginTokenIssuer, responses LoginResponseBuilder, finalization LoginFinalizer) *PasswordSessions {
	return &PasswordSessions{admission, tokens, responses, finalization}
}

// Login preserves the workflow's transaction boundaries: credential/username
// writes precede OTP; response construction may create card configuration; final
// login/deletion writes follow it. A failure retains earlier committed invariants
// but never returns a response containing tokens. The transport owns cookies and
// must deliver them only after this method succeeds. Audit identity is trusted
// request metadata and remains independent of the account being authenticated.
func (s *PasswordSessions) Login(ctx context.Context, identifier, password any, otp any, present bool, audit domain.LoginAuditContext) (*dto.LoginResponse, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	account, err := s.admission.Admit(ctx, identifier, password, otp, present, audit)
	if err != nil {
		return nil, err
	}
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	if account == nil || account.ID == "" {
		return nil, ErrLoginSessionInvariant
	}
	pair, err := s.tokens.Issue(account.ID, account.Role, account.Email)
	if err != nil {
		return nil, err
	}
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	result, err := s.responses.Build(ctx, *account, pair, audit)
	if err != nil {
		return nil, err
	}
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	if result == nil || result.UserData.UserID != account.ID || result.Pair != pair {
		return nil, ErrLoginSessionInvariant
	}
	if err = s.finalization.Finalize(ctx, account.ID, audit); err != nil {
		return nil, err
	}
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	return result, nil
}
