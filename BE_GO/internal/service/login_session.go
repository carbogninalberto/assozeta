package service

import (
	"context"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type SocialSessionAdmission interface {
	Admit(context.Context, any, any, any, any, bool, domain.LoginAuditContext) (*domain.Account, error)
}
type LoginSessions struct {
	passwords    PasswordSessionAdmission
	social       SocialSessionAdmission
	tokens       LoginTokenIssuer
	responses    LoginResponseBuilder
	finalization LoginFinalizer
}

func NewLoginSessions(passwords PasswordSessionAdmission, social SocialSessionAdmission, tokens LoginTokenIssuer, responses LoginResponseBuilder, finalization LoginFinalizer) *LoginSessions {
	return &LoginSessions{passwords, social, tokens, responses, finalization}
}

// LoginRequest consumes the already selected transport branch. Both credential
// paths retain raw fields and must finish admission before shared session effects.
func (s *LoginSessions) LoginRequest(ctx context.Context, request dto.LoginRequest, audit domain.LoginAuditContext) (*dto.LoginResponse, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	var account *domain.Account
	var err error
	switch request.Mode {
	case "password":
		account, err = s.passwords.Admit(ctx, request.Identifier, request.Password, request.OTP, request.OTPPresent, audit)
	case "social":
		account, err = s.social.Admit(ctx, request.Identifier, request.Token, request.Backend, request.OTP, request.OTPPresent, audit)
	default:
		return nil, ErrLoginSessionInvariant
	}
	if err != nil {
		return nil, err
	}
	return completeLoginSession(ctx, account, s.tokens, s.responses, s.finalization, audit)
}
