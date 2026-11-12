package dto

import (
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
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

// PythonLower is str.lower() (pystr.Lower).
func PythonLower(text string) string { return pystr.Lower(text) }

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
