package crypto

import (
	"context"
	"errors"
	"testing"

	"golang.org/x/crypto/bcrypt"
)

func TestDjangoModelPassword(t *testing.T) {
	v := NewPasswordVerifier(DefaultPasswordLimits())
	encoded, err := v.Encode(context.Background(), "123")
	if err != nil {
		t.Fatal(err)
	}
	raw, err := bcrypt.GenerateFromPassword([]byte("123"), 4)
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name  string
		value LoginPasswordValue
		hash  string
		match bool
	}{
		{"text", LoginPasswordValue{Text: "123", String: true}, encoded, true},
		{"numeric PBKDF2", LoginPasswordValue{Text: "123"}, encoded, true},
		{"incorrect numeric", LoginPasswordValue{Text: "124"}, encoded, false},
		{"null", LoginPasswordValue{Null: true}, encoded, false},
		{"raw bcrypt denied", LoginPasswordValue{Text: "123", String: true}, string(raw), false},
		{"unusable", LoginPasswordValue{Text: "123", String: true}, "!synthetic", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, err := v.CheckDjangoPassword(context.Background(), tc.value, tc.hash)
			if err != nil || got.Match != tc.match || got.Legacy {
				t.Fatalf("decision=%+v err=%v", got, err)
			}
		})
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := v.CheckDjangoPassword(ctx, LoginPasswordValue{Null: true}, encoded); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
}
