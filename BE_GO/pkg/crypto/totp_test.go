package crypto

import (
	"testing"
	"time"
)

func TestTOTPTimeBoundariesAndKnownCodes(t *testing.T) {
	secret := "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
	for _, tc := range []struct {
		at   int64
		code string
	}{{0, "755224"}, {29, "755224"}, {30, "287082"}, {59, "287082"}, {60, "359152"}, {1111111109, "081804"}, {1111111111, "050471"}, {1234567890, "005924"}, {2000000000, "279037"}, {20000000000, "353130"}} {
		code, err := TOTPCode(secret, time.Unix(tc.at, 0))
		if err != nil || code != tc.code {
			t.Fatalf("wrong code at %d", tc.at)
		}
	}
	for _, code := range []string{"287082", "２８７０８２", "²⁸⁷⁰⁸²"} {
		ok, err := VerifyTOTP(secret, code, time.Unix(59, 999999999))
		if err != nil || !ok {
			t.Fatal("valid normalized code rejected")
		}
	}
	for _, code := range []string{"755224", "359152", "287083", "287082 ", " 287082"} {
		ok, err := VerifyTOTP(secret, code, time.Unix(59, 0))
		if err != nil || ok {
			t.Fatal("adjacent or invalid code accepted")
		}
	}
}

func TestTOTPSecretSyntax(t *testing.T) {
	for _, secret := range []string{"!", "A", "A1AAAAAA", "A0AAAAAA", "MY======\n", "MY======\r", "M Y=====", "ſY", "MY=======", "=MY====="} {
		if _, err := TOTPCode(secret, time.Unix(59, 0)); err == nil {
			t.Fatal("invalid base32 secret accepted")
		}
	}
	for _, tc := range []struct{ secret, code string }{{"", "812658"}, {"MY", "300412"}, {"MZ", "300412"}, {"my======", "300412"}} {
		code, err := TOTPCode(tc.secret, time.Unix(59, 0))
		if err != nil || code != tc.code {
			t.Fatal("historical base32 syntax changed")
		}
	}
	if _, err := TOTPCode("MY", time.Unix(-30, 0)); err != ErrTOTPCounter {
		t.Fatal("negative counter accepted")
	}
}
