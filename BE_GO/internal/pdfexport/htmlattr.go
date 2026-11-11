package pdfexport

import "strings"

// invalidCharrefs and invalidCodepoints are html._invalid_charrefs and
// html._invalid_codepoints.
var invalidCharrefs = map[int]string{
	0x00: "�", 0x0d: "\r", 0x80: "€", 0x81: "\x81", 0x82: "‚", 0x83: "ƒ", 0x84: "„", 0x85: "…",
	0x86: "†", 0x87: "‡", 0x88: "ˆ", 0x89: "‰", 0x8a: "Š", 0x8b: "‹", 0x8c: "Œ", 0x8d: "\x8d",
	0x8e: "Ž", 0x8f: "\x8f", 0x90: "\x90", 0x91: "‘", 0x92: "’", 0x93: "“", 0x94: "”", 0x95: "•",
	0x96: "–", 0x97: "—", 0x98: "˜", 0x99: "™", 0x9a: "š", 0x9b: "›", 0x9c: "œ", 0x9d: "\x9d",
	0x9e: "ž", 0x9f: "Ÿ",
}

func invalidCodepoint(n int) bool {
	switch {
	case n >= 0x1 && n <= 0x8, n >= 0xe && n <= 0x1f, n >= 0x7f && n <= 0x9f, n >= 0xfdd0 && n <= 0xfdef, n == 0xb:
		return true
	}
	return n&0xfffe == 0xfffe && n <= 0x10ffff
}

// numericCharref is html._replace_charref for '#...' (without the '&').
func numericCharref(ref string) string {
	ref = strings.TrimSuffix(ref, ";")
	base := 10
	digits := ref[1:]
	if digits[0] == 'x' || digits[0] == 'X' {
		base, digits = 16, digits[1:]
	}
	digits = strings.TrimLeft(digits, "0")
	n := 0x110000
	if len(digits) <= 8 {
		n = 0
		for _, c := range digits {
			d := int(c - '0')
			if c >= 'a' {
				d = int(c-'a') + 10
			} else if c >= 'A' {
				d = int(c-'A') + 10
			}
			n = n*base + d
		}
	}
	if text, ok := invalidCharrefs[n]; ok {
		return text
	}
	if n >= 0xD800 && n <= 0xDFFF || n > 0x10FFFF {
		return "�"
	}
	if invalidCodepoint(n) {
		return ""
	}
	return string(rune(n))
}

// unescapeAttribute is _unescape_attrvalue (CPython 3.13.16): numeric
// references always, named ones only when they match exactly and no '='
// follows.
func unescapeAttribute(s string) string {
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
		j := i + 1
		numeric := false
		switch {
		case j < len(s) && s[j] == '#':
			k := j + 1
			if k < len(s) && (s[k] == 'x' || s[k] == 'X') {
				m := k + 1
				for m < len(s) && (s[m] >= '0' && s[m] <= '9' || s[m] >= 'a' && s[m] <= 'f' || s[m] >= 'A' && s[m] <= 'F') {
					m++
				}
				if m > k+1 {
					j, numeric = m, true
				}
			}
			if !numeric {
				m := k
				for m < len(s) && s[m] >= '0' && s[m] <= '9' {
					m++
				}
				if m > k {
					j, numeric = m, true
				}
			}
			if !numeric {
				out.WriteByte('&')
				i++
				continue
			}
		case j < len(s) && (s[j] >= 'a' && s[j] <= 'z' || s[j] >= 'A' && s[j] <= 'Z'):
			for j < len(s) && (s[j] >= 'a' && s[j] <= 'z' || s[j] >= 'A' && s[j] <= 'Z' || s[j] >= '0' && s[j] <= '9') {
				j++
			}
		default:
			out.WriteByte('&')
			i++
			continue
		}
		name := s[i+1 : j]
		suffix := ""
		if j < len(s) && (s[j] == ';' || s[j] == '=') {
			suffix = string(s[j])
			j++
		}
		switch {
		case numeric:
			// unescape() of '&#65=' leaves the '=' after the character.
			out.WriteString(numericCharref(name))
			out.WriteString(strings.TrimPrefix(suffix, ";"))
		case suffix != "=" && html5Entities[name+suffix] != "":
			out.WriteString(html5Entities[name+suffix])
		default:
			out.WriteString(s[i:j])
		}
		i = j
	}
	return out.String()
}
