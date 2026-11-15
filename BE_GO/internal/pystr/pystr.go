package pystr

import (
	"strings"
	"unicode/utf8"
)

// Runes decodes s into Python code points. The three-byte form of a lone
// surrogate (0xED 0xA0..0xBF 0x80..0xBF) becomes that surrogate code point;
// any other invalid byte becomes U+FFFD.
func Runes(s string) []rune {
	out := make([]rune, 0, len(s))
	for i := 0; i < len(s); {
		if c := s[i]; c < utf8.RuneSelf {
			out = append(out, rune(c))
			i++
			continue
		}
		if s[i] == 0xED && i+2 < len(s) && s[i+1] >= 0xA0 && s[i+1] <= 0xBF && s[i+2] >= 0x80 && s[i+2] <= 0xBF {
			out = append(out, 0xD800|rune(s[i+1]&0x3F)<<6|rune(s[i+2]&0x3F))
			i += 3
			continue
		}
		r, size := utf8.DecodeRuneInString(s[i:])
		out = append(out, r)
		i += size
	}
	return out
}

// RunesReplace decodes s into the Python code points it stands for after
// bytes.decode('utf-8', errors='replace'): the three-byte form of a lone
// surrogate (0xED 0xA0..0xBF 0x80..0xBF) becomes that surrogate code point,
// exactly as in Runes, and any other invalid UTF-8 sequence becomes one
// U+FFFD per maximal invalid subpart (Unicode Table 3-7). A Go range or
// utf8.RuneCountInString would instead yield one U+FFFD per invalid byte.
func RunesReplace(s string) []rune {
	out := make([]rune, 0, len(s))
	for i := 0; i < len(s); {
		if c := s[i]; c < utf8.RuneSelf {
			out = append(out, rune(c))
			i++
			continue
		}
		if s[i] == 0xED && i+2 < len(s) && s[i+1] >= 0xA0 && s[i+1] <= 0xBF && s[i+2] >= 0x80 && s[i+2] <= 0xBF {
			out = append(out, 0xD800|rune(s[i+1]&0x3F)<<6|rune(s[i+2]&0x3F))
			i += 3
			continue
		}
		if r, size := utf8.DecodeRuneInString(s[i:]); r != utf8.RuneError || size > 1 {
			out = append(out, r)
			i += size
			continue
		}
		out = append(out, utf8.RuneError)
		i += invalidPrefix(s[i:])
	}
	return out
}

// invalidPrefix is the length of the maximal subpart of an ill-formed UTF-8
// sequence starting at text[0] (Unicode Table 3-7).
func invalidPrefix(text string) int {
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

// String encodes Python code points. A surrogate rune is written as its
// three-byte form, so Runes(String(r)) == r.
func String(r []rune) string {
	plain := true
	for _, c := range r {
		if c >= 0xD800 && c <= 0xDFFF {
			plain = false
			break
		}
	}
	if plain {
		return string(r)
	}
	var b strings.Builder
	b.Grow(len(r) * 3)
	for _, c := range r {
		if c >= 0xD800 && c <= 0xDFFF {
			b.WriteByte(0xE0 | byte(c>>12))
			b.WriteByte(0x80 | byte(c>>6&0x3F))
			b.WriteByte(0x80 | byte(c&0x3F))
			continue
		}
		b.WriteRune(c)
	}
	return b.String()
}

// IsSpace is str.isspace() on a code point.
func IsSpace(r rune) bool {
	switch {
	case r >= 0x09 && r <= 0x0D, r >= 0x1C && r <= 0x20, r == 0x85, r == 0xA0, r == 0x1680,
		r >= 0x2000 && r <= 0x200A, r == 0x2028, r == 0x2029, r == 0x202F, r == 0x205F, r == 0x3000:
		return true
	}
	return false
}
