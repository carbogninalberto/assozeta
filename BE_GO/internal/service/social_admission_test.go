package service

import (
	"context"
	"errors"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"strings"
	"testing"
)

type socialAdmissionSteps struct {
	admissionSteps
	err   error
	empty bool
}

func (s *socialAdmissionSteps) Select(context.Context, any, any, any) (*domain.Account, error) {
	s.events = append(s.events, "social")
	if s.err != nil {
		return nil, s.err
	}
	if s.empty {
		return nil, nil
	}
	return &domain.Account{ID: "target", Password: "private"}, nil
}
func TestSocialAdmissionUsesNormalizationBeforeOTP(t *testing.T) {
	for _, outcome := range []string{"success", "social", "otp", "nil"} {
		steps := &socialAdmissionSteps{}
		if outcome == "social" {
			steps.err = ErrSocialToken
		}
		if outcome == "otp" {
			steps.otpErr = ErrOTPInvalid
		}
		if outcome == "nil" {
			steps.empty = true
		}
		account, err := NewSocialAdmission(steps, steps, steps).Admit(context.Background(), "identifier", "opaque", "google", nil, false, domain.LoginAuditContext{})
		if outcome == "success" {
			if err != nil || account == nil || account.Username != "MIXED" || account.Password != "" {
				t.Fatal("admission response")
			}
		} else if err == nil || account != nil {
			t.Fatal("failed admission exposed account")
		}
		want := "social,normalized-and-committed,otp"
		if outcome == "social" || outcome == "nil" {
			want = "social"
		}
		if strings.Join(steps.events, ",") != want {
			t.Fatal("wrong admission order")
		}
		if outcome == "social" && !errors.Is(err, ErrSocialToken) {
			t.Fatal("provider error changed")
		}
		if outcome == "otp" && !errors.Is(err, ErrOTPInvalid) {
			t.Fatal("OTP error changed")
		}
	}
}
