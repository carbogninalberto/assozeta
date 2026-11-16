package smtpsend

import (
	"errors"
	"reflect"
	"testing"
)

// pythonRunes reads request WTF-8 surrogates as one code point and decodes
// every other invalid byte the way Python's UTF-8 filesystem decoding does
// ('surrogateescape': U+DC00|byte).
func TestPythonRunesSurrogateescape(t *testing.T) {
	cases := map[string]struct {
		text  string
		runes []rune
	}{
		"env latin-1":     {"Assozeta \xe9", append([]rune("Assozeta "), 0xDCE9)},
		"two invalid":     {"\xe9\xe9", []rune{0xDCE9, 0xDCE9}},
		"truncated":       {"\xe2\x82", []rune{0xDCE2, 0xDC82}},
		"invalid middle":  {"a\xffb", []rune{'a', 0xDCFF, 'b'}},
		"valid UTF-8":     {"Città", []rune("Città")},
		"WTF-8 surrogate": {"\xed\xa0\x80", []rune{0xD800}},
		"WTF-8 low":       {"\xed\xb2\x80", []rune{0xDC80}},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			if got := pythonRunes(tc.text); !reflect.DeepEqual(got, tc.runes) {
				t.Fatalf("pythonRunes(%q) = %U, want %U", tc.text, got, tc.runes)
			}
		})
	}
}

// foldHeaderLine on raw environment bytes. The expected lines are CPython
// 3.13's:
//
//	Header(raw.decode('utf-8', 'surrogateescape'), charset=UNKNOWN8BIT,
//	    header_name=name).encode(linesep='\n', maxlinelen=0)
func TestFoldHeaderEnvironmentBytes(t *testing.T) {
	cases := map[string]struct {
		value  string
		folded string
	}{
		"Assozeta e9":  {"Assozeta \xe9", "=?unknown-8bit?q?Assozeta_=E9?="},
		"two e9":       {"\xe9\xe9", "=?unknown-8bit?b?6ek=?="},
		"a ff b":       {"a\xffb", "=?unknown-8bit?b?Yf9i?="},
		"truncated e2": {"Polisportiva \xe2\x82", "=?unknown-8bit?q?Polisportiva_=E2=82?="},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			for _, header := range []string{"From", "Subject"} {
				got, err := foldHeaderLine(header, tc.value, 0)
				if err != nil {
					t.Fatalf("%s: %v", header, err)
				}
				if got != tc.folded {
					t.Fatalf("%s = %q, want %q", header, got, tc.folded)
				}
			}
		})
	}
}

// A value with both valid non-ASCII text and an escaped byte reaches
// unknown-8bit's ascii/surrogateescape encoding, which Python refuses (the
// returned error matches ErrRaises).
func TestFoldHeaderEnvironmentBytesRaises(t *testing.T) {
	for name, value := range map[string]string{
		"latin-1 text and byte": "Città \xff",
		"euro and byte":         "\xe2\x82\xac \xe9",
		"WTF-8 high surrogate":  "\xed\xa0\x80",
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := foldHeaderLine("Subject", value, 0); !errors.Is(err, ErrRaises) {
				t.Fatalf("foldHeaderLine(%q) = %v, want ErrRaises", value, err)
			}
		})
	}
}

// djangoTextBytes is text.encode('utf-8', 'surrogateescape'): the environment
// bytes survive the body path unchanged.
func TestDjangoTextBytesSurrogateescape(t *testing.T) {
	for _, raw := range []string{"Assozeta \xe9", "\xe9\xe9", "a\xffb", "Città"} {
		got, err := djangoTextBytes(raw)
		if err != nil {
			t.Fatalf("djangoTextBytes(%q): %v", raw, err)
		}
		if string(got) != raw {
			t.Fatalf("djangoTextBytes(%q) = %q", raw, got)
		}
	}
	if _, err := djangoTextBytes("\xed\xa0\x80"); !errors.Is(err, ErrRaises) {
		t.Fatalf("lone surrogate: %v, want ErrRaises", err)
	}
}
