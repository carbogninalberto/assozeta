package pystr

import (
	"reflect"
	"testing"
)

// The vectors are bytes.decode('utf-8', errors='replace') from CPython 3.13,
// except the three-byte surrogate forms, which answer the code point a Python
// str carries when a lone surrogate is encoded that way (WTF-8).
func TestRunesReplace(t *testing.T) {
	for _, item := range []struct {
		in   string
		want []rune
	}{
		{"", []rune{}},
		{"abc", []rune{'a', 'b', 'c'}},
		{"\u00e9\u4e2d\u20ac", []rune{0xE9, 0x4E2D, 0x20AC}},
		{"\xf0\x9f\x98\x80", []rune{0x1F600}},
		{"\xf0\x90\x80\x80", []rune{0x10000}},
		{"\xed\xa0\x80", []rune{0xD800}},
		{"a\xed\xbf\xbfb", []rune{'a', 0xDFFF, 'b'}},
		{"\xed\x9f\xbf", []rune{0xD7FF}},
		{"\xe2\x82", []rune{0xFFFD}},
		{"\xff", []rune{0xFFFD}},
		{"\xf0\x90\x80", []rune{0xFFFD}},
		{"\xe2\x82A", []rune{0xFFFD, 'A'}},
		{"\xed\xa0", []rune{0xFFFD, 0xFFFD}},
		{"\xed\xa0A", []rune{0xFFFD, 0xFFFD, 'A'}},
		{"\xc0\x80", []rune{0xFFFD, 0xFFFD}},
		{"\xe0\x80\x80", []rune{0xFFFD, 0xFFFD, 0xFFFD}},
		{"\xf5", []rune{0xFFFD}},
		{"\xc2", []rune{0xFFFD}},
		{"\xf4\x90\x80\x80", []rune{0xFFFD, 0xFFFD, 0xFFFD, 0xFFFD}},
		{"2020-01-03\xff", []rune{'2', '0', '2', '0', '-', '0', '1', '-', '0', '3', 0xFFFD}},
	} {
		if got := RunesReplace(item.in); !reflect.DeepEqual(got, item.want) {
			t.Errorf("RunesReplace(% x) = %v, want %v", item.in, got, item.want)
		}
	}
}

// RunesReplace differs from Runes only in how it consumes invalid bytes.
func TestRunesReplaceRoundTrip(t *testing.T) {
	for _, r := range [][]rune{
		{},
		{'a'},
		{0xD800},
		{0xDC00, 'x'},
		{0xE9, 0x4E2D, 0x1F600, 0xFFFD},
	} {
		if got := RunesReplace(String(r)); !reflect.DeepEqual(got, r) {
			t.Errorf("RunesReplace(String(%v)) = %v", r, got)
		}
	}
}
