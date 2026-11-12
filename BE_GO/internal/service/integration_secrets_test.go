package service

import (
	"errors"
	"testing"
)

// Tokens produced by Python cryptography's Fernet with decrypt_secret's key derivation.
func TestDecryptIntegrationSecretMatchesPythonFernet(t *testing.T) {
	const secret = "test-secret"
	got, err := DecryptIntegrationSecret("gAAAAABq4UxlEUnkk7VzJb-4htMs7TK555_ZV3OA7PhevFW69mEbcTPSGlf4gdQE6LpB6CLjhu3XxP5ObatrvB-YmWGH-m10NQ==", []string{secret})
	if err != nil || got != "sk_test_è" {
		t.Fatalf("got %q %v", got, err)
	}
	if got, err = DecryptIntegrationSecret("gAAAAABq4UxlvUZrs4WAWhT7mkpVFE4yIoRbPpJSgUBCSW03pTyrDcyJjuEiKBjGzJHqitlGhMHlM7OuHJgrg2t5Lzh6gWw2TA==", []string{secret}); err != nil || got != "" {
		t.Fatalf("empty: got %q %v", got, err)
	}
	other := "gAAAAABq4UxlOLW_kmJTl5TdwyBLTlk4c6mYkw8DAJ6H7nVKigTyzJ4f8-g3LLTomGzTXXnf9S1GW6BSh-ikCIKoig11b0SvpQ=="
	if _, err = DecryptIntegrationSecret(other, []string{secret}); !errors.Is(err, ErrIntegrationSecret) {
		t.Fatalf("wrong key: %v", err)
	}
	if got, err = DecryptIntegrationSecret(other, []string{secret, "other"}); err != nil || got != "x" {
		t.Fatalf("fallback: got %q %v", got, err)
	}
	if got, err = DecryptIntegrationSecret("", nil); err != nil || got != "" {
		t.Fatalf("blank: %q %v", got, err)
	}
	if _, err = DecryptIntegrationSecret("not-a-token", []string{secret}); !errors.Is(err, ErrIntegrationSecret) {
		t.Fatalf("garbage: %v", err)
	}
}
