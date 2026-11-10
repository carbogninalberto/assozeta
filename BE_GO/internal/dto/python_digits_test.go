package dto

import (
	"testing"
	"unicode"
)

func TestPythonDigits(t *testing.T) {
	// unicodedata.decimal of digits from several scripts.
	for r, want := range map[rune]int{'7': 7, '\u0668': 8, '\uff11': 1, '\u096b': 5, '\u0663': 3, '\U0001d7d7': 9, '\U0001d7ce': 0,
		'\U0001d7ff': 9, '\u1049': 9, '\U00011066': 0} {
		if got, ok := PythonDigitValue(r); !ok || got != want {
			t.Fatalf("%U: %d %v, want %d", r, got, ok, want)
		}
	}
	for _, r := range []rune{'a', '\u00b2', '\u2460', '\u4e00', '\u00bd'} {
		if _, ok := PythonDigitValue(r); ok {
			t.Fatalf("%U is not a decimal digit", r)
		}
	}
	for _, block := range unicode.Nd.R16 {
		if block.Stride != 1 || (int(block.Hi)-int(block.Lo)+1)%10 != 0 {
			t.Fatalf("range %U-%U is not runs of ten", block.Lo, block.Hi)
		}
	}
	for _, block := range unicode.Nd.R32 {
		if block.Stride != 1 || (int(block.Hi)-int(block.Lo)+1)%10 != 0 {
			t.Fatalf("range %U-%U is not runs of ten", block.Lo, block.Hi)
		}
	}
	if got := PythonASCIIDigits("\uff12\u0660\uff12\u0666-03-1\u096b \u00b2"); got != "2026-03-15 \u00b2" {
		t.Fatal(got)
	}
}
