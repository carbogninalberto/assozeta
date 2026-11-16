package pdfrender

import "testing"

// TestServerRefusesHeader: the Content-Disposition Django 5.2's
// ResponseHeaders writes for "inline; filename=" + name, and whether uvicorn's
// HEADER_VALUE_RE ([\x00-\x08\x0a-\x1f\x7f]) refuses those bytes (the
// connection is closed). Values from CPython 3.13 and Django 5.2.18.
func TestServerRefusesHeader(t *testing.T) {
	for _, test := range []struct {
		name    string
		refused bool
		bytes   string
	}{
		{"a\x01b.pdf", true, "inline; filename=a\x01b.pdf"},
		{"a\x7fb.pdf", true, "inline; filename=a\x7fb.pdf"},
		{"a\u0085b.pdf", false, "inline; filename=a\x85b.pdf"},
		{"a\tb.pdf", false, "inline; filename=a\tb.pdf"},
		{"a\x1cb.pdf", true, "inline; filename=a\x1cb.pdf"},
		{"a\x0bb.pdf", true, "inline; filename=a\x0bb.pdf"},
		{"€\x01.pdf", false, "=?utf-8?b?aW5saW5lOyBmaWxlbmFtZT3igqwBLnBkZg==?="},
		{"€\u0085.pdf", true, ""},
		{"€ .pdf", true, ""},
		{"€\x1c.pdf", true, ""},
		{"€\x0b.pdf", true, ""},
		{"€\x1f.pdf", false, "=?utf-8?b?aW5saW5lOyBmaWxlbmFtZT3igqwfLnBkZg==?="},
		{"€\x7f.pdf", false, "=?utf-8?b?aW5saW5lOyBmaWxlbmFtZT3igqx/LnBkZg==?="},
		{"€\t.pdf", false, "=?utf-8?b?aW5saW5lOyBmaWxlbmFtZT3igqwJLnBkZg==?="},
		{"café.pdf", false, "inline; filename=caf\xe9.pdf"},
		{"日本.pdf", false, "=?utf-8?b?aW5saW5lOyBmaWxlbmFtZT3ml6XmnKwucGRm?="},
		{"a b.pdf", true, ""},
		{"€\x0c.pdf", true, ""},
	} {
		if got := ServerRefusesHeader(test.name); got != test.refused {
			t.Errorf("ServerRefusesHeader(%q) = %v, want %v", test.name, got, test.refused)
		}
		if !test.refused {
			if got := HeaderBytes("inline; filename=" + test.name); got != test.bytes {
				t.Errorf("HeaderBytes(%q) = %q, want %q", test.name, got, test.bytes)
			}
		}
	}
}
