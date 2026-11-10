package dto

import (
	"strings"
	"unicode"
)

// PythonDigitValue is unicodedata.decimal(r): the value of a decimal digit of
// any script (category Nd). The Nd ranges are runs of ten starting at a zero.
func PythonDigitValue(r rune) (int, bool) {
	if r >= '0' && r <= '9' {
		return int(r - '0'), true
	}
	for _, block := range unicode.Nd.R16 {
		if rune(block.Lo) <= r && r <= rune(block.Hi) {
			return int(r-rune(block.Lo)) % 10, true
		}
	}
	for _, block := range unicode.Nd.R32 {
		if rune(block.Lo) <= r && r <= rune(block.Hi) {
			return int(r-rune(block.Lo)) % 10, true
		}
	}
	return 0, false
}

// PythonASCIIDigits writes every decimal digit of text as its ASCII digit,
// the way int() and strptime read them.
func PythonASCIIDigits(text string) string {
	return strings.Map(func(r rune) rune {
		if r <= unicode.MaxASCII {
			return r
		}
		if value, ok := PythonDigitValue(r); ok {
			return rune('0' + value)
		}
		return r
	}, text)
}
