package service

import (
	"strings"
	"testing"
)

func TestPaymentInfoPythonUUIDText(t *testing.T) {
	for _, raw := range []string{"+" + strings.Repeat("f", 31), " " + strings.Repeat("f", 31), strings.Repeat("f", 31) + " ", "f_" + strings.Repeat("f", 30)} {
		id, err := paymentInfoParseUUID(raw)
		if err != nil || id.String() != "0fffffff-ffff-ffff-ffff-ffffffffffff" {
			t.Fatalf("%q: %v %v", raw, id, err)
		}
	}
	for _, tc := range []struct{ raw, want string }{
		{strings.Repeat("０", 32), "00000000-0000-0000-0000-000000000000"},
		{"0x" + strings.Repeat("f", 30), "00ffffff-ffff-ffff-ffff-ffffffffffff"},
		{"0x_" + strings.Repeat("f", 29), "000fffff-ffff-ffff-ffff-ffffffffffff"},
	} {
		id, err := paymentInfoParseUUID(tc.raw)
		if err != nil || id.String() != tc.want {
			t.Fatalf("%q: %v %v", tc.raw, id, err)
		}
	}
	for _, tc := range []struct{ raw, want string }{
		{"bad", "badly formed hexadecimal UUID string"},
		{strings.Repeat("g", 32), "invalid literal for int() with base 16: '" + strings.Repeat("g", 32) + "'"},
		{"'" + strings.Repeat("g", 31), "invalid literal for int() with base 16: \"'" + strings.Repeat("g", 31) + "\""},
		{"\\" + strings.Repeat("g", 31), "invalid literal for int() with base 16: '\\\\" + strings.Repeat("g", 31) + "'"},
		{"\x1c" + strings.Repeat("f", 31), "invalid literal for int() with base 16: '\\x1c" + strings.Repeat("f", 31) + "'"},
	} {
		_, err := paymentInfoParseUUID(tc.raw)
		if err == nil || err.Error() != tc.want {
			t.Fatalf("%q: %v want %q", tc.raw, err, tc.want)
		}
	}
}

func TestPaymentInfoPython151PrintableUUIDErrors(t *testing.T) {
	// Pinned Python3.13.16 includes Unicode15.1 additions absent from Go's
	// Unicode15.0 tables. repr must retain these characters, not escape them.
	for _, r := range []rune{0x2ffc, 0x2fff, 0x31ef, 0x2ebf0, 0x2ee5d} {
		raw := string(r) + strings.Repeat("g", 31)
		_, err := paymentInfoParseUUID(raw)
		want := "invalid literal for int() with base 16: '" + raw + "'"
		if err == nil || err.Error() != want {
			t.Fatalf("U+%04X: %v want %q", r, err, want)
		}
	}
	for _, tc := range []struct{ raw, want string }{
		{"\U0002ebef", `'\U0002ebef'`},
		{"\U0002ee5e", `'\U0002ee5e'`},
		{"\u31ee", `'\u31ee'`},
		{"\u3000", `'\u3000'`},
	} {
		if got := paymentInfoPythonQuote(tc.raw); got != tc.want {
			t.Fatalf("%q: %s want %s", tc.raw, got, tc.want)
		}
	}
}
