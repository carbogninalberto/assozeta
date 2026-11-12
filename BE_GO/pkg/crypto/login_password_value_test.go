package crypto

import (
	"context"
	"errors"
	"testing"
)

func TestLoginRawPasswordDecision(t *testing.T) {
	v := NewPasswordVerifier(DefaultPasswordLimits())
	ctx := context.Background()
	encoded, err := v.Encode(ctx, "123")
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name        string
		value       LoginPasswordValue
		encoded     string
		match, fail bool
	}{
		{"text", LoginPasswordValue{Text: "123", String: true}, encoded, true, false},
		{"number matches current PBKDF2", LoginPasswordValue{Text: "123"}, encoded, true, false},
		{"wrong number reaches legacy encode error", LoginPasswordValue{Text: "124"}, encoded, false, true},
		{"null reaches legacy encode error", LoginPasswordValue{Text: "None", Null: true}, encoded, false, true},
		{"unusable reaches legacy encode error", LoginPasswordValue{Text: "123"}, "!unused", false, true},
		{"unknown reaches legacy encode error", LoginPasswordValue{Text: "123"}, "retired$hash", false, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, err := v.CheckLoginValue(ctx, tc.value, tc.encoded)
			if got.Match != tc.match || errors.Is(err, ErrLoginPasswordValue) != tc.fail || got.Upgrade || got.Legacy {
				t.Fatal("wrong raw credential decision")
			}
		})
	}
	cancelled, cancel := context.WithCancel(ctx)
	cancel()
	if _, err := v.CheckLoginValue(cancelled, LoginPasswordValue{}, encoded); !errors.Is(err, context.Canceled) {
		t.Fatal("cancel ignored")
	}
}
