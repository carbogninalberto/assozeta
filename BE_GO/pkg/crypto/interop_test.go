//go:build interop

package crypto

import (
	"context"
	"encoding/json"
	"os"
	"testing"
)

func TestDjangoPasswordInteroperability(t *testing.T) {
	path := os.Getenv("ASSOZETA_CREDENTIAL_FIXTURE")
	if path == "" {
		t.Fatal("interop requires TESTS/adapters/django/credentials.py; no skipped test")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal("cannot read temporary fixture")
	}
	var fixture struct {
		Passwords []struct {
			Name, Password, Encoded string
			Valid                   bool
		} `json:"passwords"`
	}
	if json.Unmarshal(raw, &fixture) != nil {
		t.Fatal("invalid fixture")
	}
	verifier := NewPasswordVerifier(DefaultPasswordLimits())
	for _, vector := range fixture.Passwords {
		t.Run(vector.Name, func(t *testing.T) {
			valid, err := verifier.Verify(context.Background(), vector.Password, vector.Encoded)
			if valid != vector.Valid || (vector.Valid && err != nil) {
				t.Fatalf("password acceptance differs: valid=%t; error=%v", valid, err)
			}
		})
	}
	passwords := []string{"", "Go-issued-è-密碼", "long-password-for-rollback-verification"}
	result := []map[string]string{}
	for _, password := range passwords {
		hash, err := verifier.Encode(context.Background(), password)
		if err != nil {
			t.Fatal(err)
		}
		result = append(result, map[string]string{"password": password, "encoded": hash})
	}
	output, err := json.Marshal(result)
	if err != nil {
		t.Fatal("cannot encode result")
	}
	if err := os.WriteFile(os.Getenv("ASSOZETA_PASSWORD_RESULT"), output, 0600); err != nil {
		t.Fatal("cannot write result")
	}
}
