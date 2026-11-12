//go:build otpintegration

package service

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

func TestSecondFactorAgainstDjango(t *testing.T) {
	path := os.Getenv("ASSOZETA_SECOND_FACTOR_FIXTURE")
	if path == "" {
		t.Fatal("run TESTS/adapters/django/accounts.py; no skipped OTP fixtures")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Cases []struct {
			Name, Code       string
			Secret           *string
			Value            json.RawMessage
			Enabled, Present bool
			At               int64
			Upload           *domain.RequestUpload
		}
	}
	if json.Unmarshal(data, &fixture) != nil || len(fixture.Cases) == 0 {
		t.Fatal("invalid synthetic OTP fixture")
	}
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			value, err := dto.ParseReferenceJSON(tc.Value)
			if err != nil {
				t.Fatal("invalid request fixture")
			}
			if tc.Upload != nil {
				value = *tc.Upload
			}
			gate := NewSecondFactor(func() time.Time { return time.Unix(tc.At, 0) })
			err = gate.Check(context.Background(), tc.Enabled, tc.Secret, value, tc.Present)
			code := ""
			switch {
			case err == nil:
			case errors.Is(err, ErrOTPRequired):
				code = "required"
			case errors.Is(err, ErrOTPInvalid):
				code = "invalid"
			default:
				code = "server_error"
			}
			if code != tc.Code {
				t.Fatalf("OTP outcome = %s; want %s", code, tc.Code)
			}
		})
	}
}
