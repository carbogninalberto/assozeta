// Package pyhtml reproduces Python 3.13's html.unescape.
package pyhtml

import (
	"math/big"
	"strings"
	"unicode/utf8"
)

// invalidCharrefs is html._invalid_charrefs.
var invalidCharrefs = map[int64]string{
	0x00: "�", 0x0d: "\r", 0x80: "€", 0x81: "\u0081", 0x82: "‚", 0x83: "ƒ", 0x84: "„", 0x85: "…",
	0x86: "†", 0x87: "‡", 0x88: "ˆ", 0x89: "‰", 0x8a: "Š", 0x8b: "‹", 0x8c: "Œ", 0x8d: "\u008d",
	0x8e: "Ž", 0x8f: "\u008f", 0x90: "\u0090", 0x91: "‘", 0x92: "’", 0x93: "“", 0x94: "”", 0x95: "•",
	0x96: "–", 0x97: "—", 0x98: "˜", 0x99: "™", 0x9a: "š", 0x9b: "›", 0x9c: "œ", 0x9d: "\u009d",
	0x9e: "ž", 0x9f: "Ÿ",
}

// invalidCodepoint is membership in html._invalid_codepoints.
func invalidCodepoint(n int64) bool {
	switch {
	case n >= 0x1 && n <= 0x8, n == 0xb, n >= 0xe && n <= 0x1f, n >= 0x7f && n <= 0x9f, n >= 0xfdd0 && n <= 0xfdef:
		return true
	}
	return n <= 0x10ffff && n&0xfffe == 0xfffe
}

// Unescape is html.unescape: the _charref pattern
// &(#[0-9]+;?|#[xX][0-9a-fA-F]+;?|[^\t\n\f <&#;]{1,32};?) replaced by
// _replace_charref.
func Unescape(s string) string {
	if !strings.Contains(s, "&") {
		return s
	}
	var out strings.Builder
	for i := 0; i < len(s); {
		if s[i] != '&' {
			out.WriteByte(s[i])
			i++
			continue
		}
		replacement, width, ok := charref(s[i+1:])
		if !ok {
			out.WriteByte('&')
			i++
			continue
		}
		out.WriteString(replacement)
		i += 1 + width
	}
	return out.String()
}

func isDigit(c byte) bool { return c >= '0' && c <= '9' }
func isHex(c byte) bool {
	return isDigit(c) || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')
}

// charref matches the group after '&' and returns its replacement and byte width.
func charref(rest string) (string, int, bool) {
	if rest == "" {
		return "", 0, false
	}
	if rest[0] == '#' {
		base, start := 10, 1
		digit := isDigit
		if len(rest) > 1 && (rest[1] == 'x' || rest[1] == 'X') {
			base, start, digit = 16, 2, isHex
		}
		end := start
		for end < len(rest) && digit(rest[end]) {
			end++
		}
		if end == start {
			// '#' or '#x' without digits: no alternative matches ('#' cannot start a name).
			return "", 0, false
		}
		number, _ := new(big.Int).SetString(rest[start:end], base)
		width := end
		if end < len(rest) && rest[end] == ';' {
			width++
		}
		return numeric(number), width, true
	}
	// [^\t\n\f <&#;]{1,32} (code points), then an optional ';'.
	end, count := 0, 0
	for end < len(rest) && count < 32 {
		r, size := utf8.DecodeRuneInString(rest[end:])
		if strings.ContainsRune("\t\n\f <&#;", r) {
			break
		}
		end += size
		count++
	}
	if count == 0 {
		return "", 0, false
	}
	if end < len(rest) && rest[end] == ';' {
		end++
	}
	name := rest[:end]
	if value, ok := html5[name]; ok {
		return value, end, true
	}
	// The longest prefix of at least two code points that is a name.
	runes := []rune(name)
	for x := len(runes) - 1; x > 1; x-- {
		if value, ok := html5[string(runes[:x])]; ok {
			return value + string(runes[x:]), end, true
		}
	}
	return "&" + name, end, true
}

func numeric(number *big.Int) string {
	if !number.IsInt64() || number.Int64() > 0x10ffff {
		return "�"
	}
	n := number.Int64()
	if value, ok := invalidCharrefs[n]; ok {
		return value
	}
	if n >= 0xd800 && n <= 0xdfff {
		return "�"
	}
	if invalidCodepoint(n) {
		return ""
	}
	return string(rune(n))
}

// Entity is html.entities.html5[name] (name with or without its ';').
func Entity(name string) (string, bool) {
	value, ok := html5[name]
	return value, ok
}
