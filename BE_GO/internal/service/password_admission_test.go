package service

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type admissionSteps struct {
	events  []string
	failure string
	otpErr  error
}

func (s *admissionSteps) Select(context.Context, string, string) (PasswordSelection, error) {
	s.events = append(s.events, "credentials")
	if s.failure != "" {
		return PasswordSelection{Failure: s.failure}, nil
	}
	return PasswordSelection{Account: &domain.Account{ID: "target", Username: "mixed"}}, nil
}
func (s *admissionSteps) Prepare(context.Context, string, domain.LoginAuditContext) (domain.LoginChallenge, error) {
	s.events = append(s.events, "normalized-and-committed")
	secret := "test-only"
	return domain.LoginChallenge{Account: domain.Account{ID: "target", Username: "MIXED", Active: true}, TwoFactorEnabled: true, TwoFactorSecret: &secret}, nil
}
func (s *admissionSteps) Check(context.Context, bool, *string, any, bool) error {
	s.events = append(s.events, "otp")
	return s.otpErr
}
func TestPasswordAdmissionOrdersCommittedPreparationBeforeOTP(t *testing.T) {
	for _, failure := range []string{"", "invalid", "not_found", "otp"} {
		steps := &admissionSteps{}
		switch failure {
		case "invalid", "not_found":
			steps.failure = failure
		case "otp":
			steps.otpErr = ErrOTPRequired
		}
		account, err := NewPasswordAdmission(steps, steps, steps).Admit(context.Background(), "mixed", "test-only", nil, false, domain.LoginAuditContext{})
		if failure == "" {
			if err != nil || account == nil || account.Username != "MIXED" {
				t.Fatal("admission did not use current normalized state")
			}
		} else if err == nil || account != nil {
			t.Fatal("failed admission returned account")
		}
		expected := "credentials,normalized-and-committed,otp"
		if steps.failure != "" {
			expected = "credentials"
		}
		if strings.Join(steps.events, ",") != expected {
			t.Fatal("login stage order changed")
		}
		if failure == "otp" && !errors.Is(err, ErrOTPRequired) {
			t.Fatal("OTP error replaced")
		}
	}
}
