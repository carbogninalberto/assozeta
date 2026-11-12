package crypto

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func TestDjangoPBKDF2AndUnusablePasswords(t *testing.T) {
	verifier := NewPasswordVerifier(DefaultPasswordLimits())
	// Fixed synthetic historical fixture, independent of the Go encoder.
	historical := "pbkdf2_sha256$1000000$syntheticfixture$TcPsoVZfg0j3DpWeu36EbtbBY9egK5Ofgyatqi7qLGw="
	if ok, err := verifier.Verify(context.Background(), "ContractPass!123", historical); err != nil || !ok {
		t.Fatal("historical credential rejected")
	}
	if ok, err := verifier.Verify(context.Background(), "wrong", historical); err != nil || ok {
		t.Fatal("historical wrong password accepted")
	}
	for _, password := range []string{"", "unicode-è-密碼", strings.Repeat("long", 40)} {
		hash, err := verifier.Encode(context.Background(), password)
		if err != nil {
			t.Fatal(err)
		}
		if !strings.HasPrefix(hash, "pbkdf2_sha256$1000000$") {
			t.Fatal("rollback-incompatible encoding")
		}
		ok, err := verifier.Verify(context.Background(), password, hash)
		if err != nil || !ok {
			t.Fatal("generated password did not verify")
		}
		ok, err = verifier.Verify(context.Background(), password+"x", hash)
		if err != nil || ok {
			t.Fatal("wrong password accepted")
		}
	}
	for _, hash := range []string{"!", "!not-usable", ""} {
		ok, _ := verifier.Verify(context.Background(), "anything", hash)
		if ok {
			t.Fatal("unusable password accepted")
		}
	}
}
func TestInvalidHashesFailWithoutPanicsOrCredentialDisclosure(t *testing.T) {
	verifier := NewPasswordVerifier(DefaultPasswordLimits())
	for _, hash := range []string{
		"md5$salt$hash", "unknown$secret", "pbkdf2_sha256$-1$salt$AAAA", "pbkdf2_sha256$0$salt$AAAA",
		"pbkdf2_sha256$1000$$AAAA", "pbkdf2_sha1$not-a-number$salt$AAAA", "bcrypt_sha256$bad",
		"scrypt$3$salt$8$1$AAAA", "scrypt$16$salt$0$1$AAAA", "scrypt$16$salt$8$0$AAAA",
		"argon2$argon2id$v=19$m=8,t=1,p=0$c2FsdHNhbHQ$aGFzaA", "argon2$argon2id$v=19$m=8,t=0,p=1$c2FsdHNhbHQ$aGFzaA",
	} {
		ok, err := verifier.Verify(context.Background(), "private-password", hash)
		if ok {
			t.Fatal("invalid hash accepted")
		}
		if err != nil && (strings.Contains(err.Error(), "private-password") || strings.Contains(err.Error(), hash)) {
			t.Fatal("credentials exposed in error")
		}
	}
}
func TestPasswordCostAndCancellationAreExplicitErrors(t *testing.T) {
	limits := DefaultPasswordLimits()
	limits.PBKDF2Iterations = 10
	verifier := NewPasswordVerifier(limits)
	// Correct digest shape, but the stored cost exceeds this explicit policy.
	ok, err := verifier.Verify(context.Background(), "test", "pbkdf2_sha256$1000000$salt$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
	if ok || !errors.Is(err, ErrPasswordCost) {
		t.Fatal("over-budget hash was not reported")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := verifier.Verify(ctx, "test", "!unusable"); !errors.Is(err, context.Canceled) {
		t.Fatal("cancellation not respected")
	}
}
func FuzzVerifyPasswordNeverPanics(f *testing.F) {
	f.Add("pbkdf2_sha256$1$salt$AAAA")
	f.Add("argon2$argon2id$v=19$m=8,t=1,p=1$c2FsdHNhbHQ$aGFzaA")
	limits := DefaultPasswordLimits()
	limits.PBKDF2Iterations = 100
	limits.ArgonMemoryKiB = 64
	limits.ArgonTime = 2
	limits.ScryptMemoryBytes = 1024
	limits.BcryptCost = 4
	verifier := NewPasswordVerifier(limits)
	f.Fuzz(func(t *testing.T, encoded string) { verifier.Verify(context.Background(), "test-password", encoded) })
}
