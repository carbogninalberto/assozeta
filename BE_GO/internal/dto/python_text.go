package dto

import (
	"strings"
	"unicode"
	"unicode/utf8"
)

// PythonUTF8Replace is bytes.decode('utf-8', errors='replace'): each maximal
// invalid subpart becomes one U+FFFD (Go's range loop yields one per byte).
func PythonUTF8Replace(raw string) string {
	if utf8.ValidString(raw) {
		return raw
	}
	var out strings.Builder
	for i := 0; i < len(raw); {
		if r, size := utf8.DecodeRuneInString(raw[i:]); r != utf8.RuneError || size > 1 {
			out.WriteString(raw[i : i+size])
			i += size
			continue
		}
		out.WriteRune(utf8.RuneError)
		i += pythonInvalidPrefix(raw[i:])
	}
	return out.String()
}

// pythonInvalidPrefix is the length of the maximal subpart of an ill-formed
// sequence starting at text[0] (Unicode Table 3-7).
func pythonInvalidPrefix(text string) int {
	first := text[0]
	low, high, need := byte(0x80), byte(0xBF), 0
	switch {
	case first >= 0xC2 && first <= 0xDF:
		need = 1
	case first == 0xE0:
		low, need = 0xA0, 2
	case first >= 0xE1 && first <= 0xEC || first == 0xEE || first == 0xEF:
		need = 2
	case first == 0xED:
		high, need = 0x9F, 2
	case first == 0xF0:
		low, need = 0x90, 3
	case first >= 0xF1 && first <= 0xF3:
		need = 3
	case first == 0xF4:
		high, need = 0x8F, 3
	default:
		return 1
	}
	size := 1
	for ; size <= need && size < len(text); size++ {
		if text[size] < low || text[size] > high {
			break
		}
		low, high = 0x80, 0xBF
	}
	return size
}

// pythonCasedLetter and pythonCaseIgnorable are the Unicode properties
// str.lower() consults for the final sigma rule.
func pythonCasedLetter(r rune) bool {
	return unicode.IsUpper(r) || unicode.IsLower(r) || unicode.IsTitle(r) || unicode.Is(unicode.Other_Lowercase, r) || unicode.Is(unicode.Other_Uppercase, r)
}

func pythonCaseIgnorable(r rune) bool {
	switch r {
	case 0x27, 0x2E, 0x3A, 0x5E, 0x60, 0xA8, 0xAD, 0xAF, 0xB4, 0xB7, 0xB8, 0x387, 0x55F, 0x5F4, 0x2018, 0x2019, 0x2024, 0x2027,
		0xFE13, 0xFE52, 0xFE55, 0xFF07, 0xFF0E, 0xFF1A, 0xFF3E, 0xFF40, 0xFFE3:
		return true
	}
	return unicode.In(r, unicode.Mn, unicode.Me, unicode.Cf, unicode.Lm, unicode.Sk)
}

// PythonLower is str.lower(): the simple lower-case mapping, U+0130 as 'i'
// with a combining dot, and the final sigma rule (SpecialCasing).
func PythonLower(text string) string {
	runes := []rune(text)
	var out strings.Builder
	for i, r := range runes {
		switch {
		case r == 0x130:
			out.WriteString("i̇")
		case r == 0x3A3:
			before := false
			for j := i - 1; j >= 0; j-- {
				if pythonCaseIgnorable(runes[j]) {
					continue
				}
				before = pythonCasedLetter(runes[j])
				break
			}
			after := false
			for j := i + 1; j < len(runes); j++ {
				if pythonCaseIgnorable(runes[j]) {
					continue
				}
				after = pythonCasedLetter(runes[j])
				break
			}
			if before && !after {
				out.WriteRune(0x3C2)
			} else {
				out.WriteRune(0x3C3)
			}
		default:
			out.WriteRune(unicode.ToLower(r))
		}
	}
	return out.String()
}

// PythonUpper is str.upper(): the full upper-case mapping of CPython's Unicode database (SpecialCasing included, so
// 'ß' is "SS"); pythonUpperTable lists every code point it changes.
func PythonUpper(text string) string {
	var out strings.Builder
	out.Grow(len(text))
	for _, r := range text {
		if mapped, ok := pythonUpperTable[r]; ok {
			out.WriteString(mapped)
		} else {
			out.WriteRune(r)
		}
	}
	return out.String()
}
