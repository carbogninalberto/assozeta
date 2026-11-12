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
