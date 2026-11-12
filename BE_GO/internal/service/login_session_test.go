package service

import (
	"context"
	"errors"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"reflect"
	"testing"
)

type socialSessionSteps struct {
	s              *sessionStages
	token, backend any
	calls          int
	err            error
}

func (s *socialSessionSteps) Admit(ctx context.Context, identifier, token, backend, otp any, present bool, audit domain.LoginAuditContext) (*domain.Account, error) {
	s.calls++
	if !reflect.DeepEqual(token, s.token) || !reflect.DeepEqual(backend, s.backend) {
		s.s.t.Fatal("social input changed")
	}
	if s.err != nil {
		return nil, s.err
	}
	return s.s.Admit(ctx, identifier, "password", otp, present, audit)
}
func TestLoginSessionDispatchAndSharedCompletion(t *testing.T) {
	for _, mode := range []string{"password", "social"} {
		t.Run(mode, func(t *testing.T) {
			s := sessionFixture(t)
			social := &socialSessionSteps{s: s, token: domain.RequestObject{{Name: "raw", Value: nil}}, backend: "google"}
			flow := NewLoginSessions(s, social, s, s, s)
			result, err := flow.LoginRequest(context.Background(), dto.LoginRequest{Mode: mode, Identifier: "identifier", Password: "password", Token: social.token, Backend: social.backend, OTP: "287082", OTPPresent: true}, s.audit)
			if err != nil || result != s.response || !reflect.DeepEqual(s.calls, []string{"admit", "issue", "build", "finalize"}) {
				t.Fatal("shared completion order")
			}
			want := 0
			if mode == "social" {
				want = 1
			}
			if social.calls != want {
				t.Fatal("wrong admission branch")
			}
		})
	}
	s := sessionFixture(t)
	social := &socialSessionSteps{s: s, err: ErrSocialToken}
	flow := NewLoginSessions(s, social, s, s, s)
	if result, err := flow.LoginRequest(context.Background(), dto.LoginRequest{Mode: "social"}, s.audit); result != nil || !errors.Is(err, ErrSocialToken) || len(s.calls) != 0 {
		t.Fatal("failed social credential produced session effects")
	}
	if result, err := flow.LoginRequest(context.Background(), dto.LoginRequest{Mode: "forged"}, s.audit); result != nil || !errors.Is(err, ErrLoginSessionInvariant) || len(s.calls) != 0 {
		t.Fatal("invalid dispatch admitted")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	before := social.calls
	if result, err := flow.LoginRequest(ctx, dto.LoginRequest{Mode: "social"}, s.audit); result != nil || !errors.Is(err, context.Canceled) || social.calls != before {
		t.Fatal("cancelled dispatch called admission")
	}
}
