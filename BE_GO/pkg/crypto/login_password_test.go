package crypto

import (
	"context"
	"testing"
)

func TestLoginPasswordUpgradePolicy(t *testing.T) {
	v := NewPasswordVerifier(DefaultPasswordLimits())
	current, err := v.Encode(context.Background(), "synthetic-password")
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name, password, encoded string
		match, upgrade, legacy  bool
	}{
		{"current", "synthetic-password", current, true, false, false},
		{"wrong", "incorrect", current, false, false, false},
		{"unusable", "synthetic-password", "!disabled", false, false, false},
		{"unknown", "synthetic-password", "retired$invalid", false, false, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			result, err := v.CheckLogin(context.Background(), tc.password, tc.encoded)
			if err != nil || result.Match != tc.match || result.Upgrade != tc.upgrade || result.Legacy != tc.legacy {
				t.Fatal("incorrect login verification decision")
			}
		})
	}
}
