package service

import (
	"context"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type SocialSelector interface {
	Select(context.Context, any, any, any) (*domain.Account, error)
}
type SocialAdmission struct {
	social       SocialSelector
	preparation  LoginPreparer
	secondFactor LoginSecondFactor
}

func NewSocialAdmission(social SocialSelector, preparation LoginPreparer, secondFactor LoginSecondFactor) *SocialAdmission {
	return &SocialAdmission{social, preparation, secondFactor}
}
func (s *SocialAdmission) Admit(ctx context.Context, identifier, token, backend, otp any, present bool, audit domain.LoginAuditContext) (*domain.Account, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	account, err := s.social.Select(ctx, identifier, token, backend)
	if err != nil {
		return nil, err
	}
	return admitLoginAccount(ctx, account, s.preparation, s.secondFactor, otp, present, audit)
}
