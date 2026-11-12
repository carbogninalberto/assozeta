package pystr

import (
	"reflect"
	"testing"
)

func TestRunesSurrogateAndInvalidBytes(t *testing.T) {
	for _, item := range []struct {
		in   string
		want []rune
	}{
		{"", []rune{}},
		{"abc", []rune{'a', 'b', 'c'}},
		{"\u00e9\u4e2d", []rune{0xE9, 0x4E2D}},
		{"\xed\xa0\x80", []rune{0xD800}},
		{"a\xed\xbf\xbfb", []rune{'a', 0xDFFF, 'b'}},
		{"\xff", []rune{0xFFFD}},
		{"\xed\xa0", []rune{0xFFFD, 0xFFFD}},
		{"\xed\xa0A", []rune{0xFFFD, 0xFFFD, 'A'}},
	} {
		if got := Runes(item.in); !reflect.DeepEqual(got, item.want) {
			t.Errorf("Runes(%q) = %v, want %v", item.in, got, item.want)
		}
	}
}

func TestStringRoundTrip(t *testing.T) {
	for _, r := range [][]rune{
		{},
		{'a'},
		{0xE9, 0x4E2D, 0x1F600},
		{0xD800},
		{'a', 0xDBFF, 'b', 0xDC00},
		{0xD800, 0xDC00}, // two surrogates, not a pair
	} {
		encoded := String(r)
		if got := Runes(encoded); !reflect.DeepEqual(got, r) {
			t.Errorf("Runes(String(%v)) = %v", r, got)
		}
	}
	if got := String([]rune{0xD800}); got != "\xed\xa0\x80" {
		t.Errorf("String(surrogate) = % x, want ed a0 80", got)
	}
	if got := String([]rune{0x10FFFF}); got != "\U0010FFFF" {
		t.Errorf("String(U+10FFFF) = %q", got)
	}
}

func TestLowerRunes(t *testing.T) {
	for _, item := range []struct{ in, want string }{
		{"ABC", "abc"},
		{"\u0130", "i\u0307"}, // U+0130 has a SpecialCasing entry
		{"\u03a3", "\u03c3"},  // sigma with nothing before it
		{"\u0391\u03a3", "\u03b1\u03c2"},
		{"\u0391\u03a3\u0391", "\u03b1\u03c3\u03b1"},
		{"\u0391\u03a3 ", "\u03b1\u03c2 "},
		{"\u03a3'", "\u03c3'"},
		{"a\u03a3", "a\u03c2"},
		{"\u212a", "k"},
		{"\xed\xa0\x80", "\xed\xa0\x80"}, // lone surrogates are unchanged
	} {
		got := String(LowerRunes(Runes(item.in)))
		if got != item.want {
			t.Errorf("LowerRunes(%q) = %q, want %q", item.in, got, item.want)
		}
	}
}

func TestLowerKeepsStrings(t *testing.T) {
	if got := Lower("A\u0130\u03a3"); got != "ai\u0307\u03c2" {
		t.Errorf("Lower = %q", got)
	}
}

// TestIsSpaceVectors compares with str.isspace() for the boundaries of every
// range in CPython's table plus nearby non-space code points.
func TestIsSpaceVectors(t *testing.T) {
	spaces := map[rune]bool{
		0x08: false, 0x09: true, 0x0A: true, 0x0B: true, 0x0C: true, 0x0D: true, 0x0E: false,
		0x1B: false, 0x1C: true, 0x1D: true, 0x1E: true, 0x1F: true, 0x20: true, 0x21: false,
		0x84: false, 0x85: true, 0x86: false, 0x9F: false, 0xA0: true, 0xA1: false,
		0x167F: false, 0x1680: true, 0x1681: false,
		0x1FFF: false, 0x2000: true, 0x200A: true, 0x200B: false,
		0x2027: false, 0x2028: true, 0x2029: true, 0x202A: false,
		0x202E: false, 0x202F: true, 0x2030: false,
		0x205E: false, 0x205F: true, 0x2060: false,
		0x2FFF: false, 0x3000: true, 0x3001: false,
		0xFEFF: false, 0xD800: false,
	}
	for r, want := range spaces {
		if got := IsSpace(r); got != want {
			t.Errorf("IsSpace(U+%04X) = %v, want %v", r, got, want)
		}
	}
	// Exactly 29 code points are spaces, all inside the table above.
	count := 0
	for r := rune(0); r <= 0x10FFFF; r++ {
		if IsSpace(r) {
			count++
		}
	}
	if count != 29 {
		t.Errorf("IsSpace matches %d code points, want 29", count)
	}
}
