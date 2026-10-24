package crypto

import (
	"bytes"
	"errors"
	"io"
	"testing"
)

func TestProvisioningSecretRequiresFullEntropy(t *testing.T) {
	secret, err := GenerateTOTPSecret(bytes.NewReader(make([]byte, 20)))
	if err != nil || secret != "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" {
		t.Fatal("unexpected base32 provisioning secret")
	}
	secret, err = GenerateTOTPSecret(bytes.NewReader(make([]byte, 19)))
	if !errors.Is(err, io.ErrUnexpectedEOF) || secret != "" {
		t.Fatal("partial entropy produced a secret")
	}
}
func TestProvisioningURI(t *testing.T) {
	for _, tc := range []struct{ name, issuer, want string }{
		{"support@example.test", "Assozeta Contract", "otpauth://totp/Assozeta%20Contract:support%40example.test?secret=ABC&issuer=Assozeta%20Contract"},
		{"", "", "otpauth://totp/Secret?secret=ABC"},
		{"None", "Bakney Sport", "otpauth://totp/Bakney%20Sport:None?secret=ABC&issuer=Bakney%20Sport"},
		{"a/b +?&é", "Club / Δ", "otpauth://totp/Club%20/%20%CE%94:a/b%20%2B%3F%26%C3%A9?secret=ABC&issuer=Club%20%2F%20%CE%94"},
	} {
		if got := TOTPProvisioningURI("ABC", tc.name, tc.issuer); got != tc.want {
			t.Fatal("provisioning URI differs", got)
		}
	}
}
