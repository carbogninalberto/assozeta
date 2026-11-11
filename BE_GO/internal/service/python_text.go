package service

import (
	"strings"
	"unicode/utf8"
)

// pythonReplaceInvalidUTF8 is bytes.decode('utf-8', 'replace'): every maximal
// ill-formed subpart becomes one U+FFFD (Django decodes query strings so).
func pythonReplaceInvalidUTF8(raw string) string {
	if utf8.ValidString(raw) {
		return raw
	}
	var out strings.Builder
	for i := 0; i < len(raw); {
		r, size := utf8.DecodeRuneInString(raw[i:])
		if r != utf8.RuneError || size > 1 {
			out.WriteString(raw[i : i+size])
			i += size
			continue
		}
		i += pythonIllFormedLength(raw[i:])
		out.WriteRune(utf8.RuneError)
	}
	return out.String()
}

// pythonIllFormedLength is the length of the maximal subpart of an ill-formed
// sequence that starts the text (Unicode Table 3-7).
func pythonIllFormedLength(text string) int {
	first := text[0]
	low, high, needed := byte(0x80), byte(0xBF), 0
	switch {
	case first >= 0xC2 && first <= 0xDF:
		needed = 1
	case first == 0xE0:
		low, needed = 0xA0, 2
	case first >= 0xE1 && first <= 0xEC, first == 0xEE, first == 0xEF:
		needed = 2
	case first == 0xED:
		high, needed = 0x9F, 2
	case first == 0xF0:
		low, needed = 0x90, 3
	case first >= 0xF1 && first <= 0xF3:
		needed = 3
	case first == 0xF4:
		high, needed = 0x8F, 3
	default:
		return 1
	}
	length := 1
	for length <= needed && length < len(text) {
		next := text[length]
		if length == 1 && (next < low || next > high) || length > 1 && (next < 0x80 || next > 0xBF) {
			break
		}
		length++
	}
	return length
}
