package service

import (
	"crypto/sha256"
	"encoding/base64"
	"testing"
)

// TestRestorablePasswordHash follows is_restorable_password_hash for the
// pbkdf2 hashers (Django 5.2: 1,000,000 iterations) and hands off others.
func TestRestorablePasswordHash(t *testing.T) {
	digest := sha256.Sum256([]byte("x"))
	good := "pbkdf2_sha256$1000000$salt$" + base64.StdEncoding.EncodeToString(digest[:])
	cases := []struct {
		encoded       string
		ok, supported bool
	}{
		{good, true, true},
		{"", false, true},
		{"!unusable", false, true},
		{"pbkdf2_sha256$1000001$salt$" + base64.StdEncoding.EncodeToString(digest[:]), false, true},
		{"pbkdf2_sha256$1000000$$" + base64.StdEncoding.EncodeToString(digest[:]), false, true},
		{"pbkdf2_sha256$1000000$salt$c2hvcnQ=", false, true},
		{"pbkdf2_sha256$10", false, true},
		{"pbkdf2_sha256$+10$salt$x", false, false},
		{"md5$salt$abc", false, false},
		{"argon2$argon2id$v=19$m=102400,t=2,p=8$c2FsdA$aGFzaA", false, false},
	}
	for _, c := range cases {
		ok, supported := restorablePasswordHash(c.encoded)
		if ok != c.ok || supported != c.supported {
			t.Fatalf("%q: got %v/%v", c.encoded, ok, supported)
		}
	}
}
