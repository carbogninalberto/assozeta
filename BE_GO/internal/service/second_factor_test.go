package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestSecondFactorAdmissionOrder(t *testing.T) {
	secret := "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
	invalid := "invalid!"
	calls := 0
	gate := NewSecondFactor(func() time.Time { calls++; return time.Unix(59, 0) })
	for _, tc := range []struct {
		name    string
		enabled bool
		secret  *string
		value   any
		present bool
		want    error
	}{
		{"disabled ignores input", false, nil, nil, true, nil},
		{"absent", true, nil, nil, false, ErrOTPRequired},
		{"short", true, &invalid, "12345", true, ErrOTPRequired},
		{"empty list", true, &invalid, []any{}, true, ErrOTPRequired},
		{"null", true, &secret, nil, true, ErrOTPShape},
		{"number", true, &secret, 123456, true, ErrOTPShape},
		{"bool", true, &secret, true, true, ErrOTPShape},
		{"valid", true, &secret, "287082", true, nil},
		{"file name and size", true, &secret, domain.RequestUpload{Name: "287082", Size: 6}, true, nil},
		{"file length controls required", true, &secret, domain.RequestUpload{Name: "287082", Size: 5}, true, ErrOTPRequired},
		{"invalid", true, &secret, "287083", true, ErrOTPInvalid},
		{"normalized fullwidth", true, &secret, "２８７０８２", true, nil},
		{"six items not digits", true, &secret, []any{1, 2, 3, 4, 5, 6}, true, ErrOTPInvalid},
		{"six object fields not digits", true, &secret, domain.RequestObject{{Name: "a"}, {Name: "b"}, {Name: "c"}, {Name: "d"}, {Name: "e"}, {Name: "f"}}, true, ErrOTPInvalid},
	} {
		t.Run(tc.name, func(t *testing.T) {
			before := calls
			err := gate.Check(context.Background(), tc.enabled, tc.secret, tc.value, tc.present)
			if !errors.Is(err, tc.want) {
				t.Fatalf("incorrect second-factor outcome: %v", err)
			}
			if (tc.want == ErrOTPRequired || tc.want == ErrOTPShape || !tc.enabled) && calls != before {
				t.Fatal("invalid shape reached OTP generation")
			}
		})
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if !errors.Is(gate.Check(ctx, true, &secret, "287082", true), context.Canceled) {
		t.Fatal("cancelled OTP check admitted")
	}
}
