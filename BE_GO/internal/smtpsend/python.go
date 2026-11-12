package smtpsend

import "strings"

func isASCII(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] >= 0x80 {
			return false
		}
	}
	return true
}

// latin1 decodes bytes as ISO-8859-1, as ehlo() does with the reply.
func latin1(b []byte) string {
	var builder strings.Builder
	for _, c := range b {
		builder.WriteRune(rune(c))
	}
	return builder.String()
}

// pythonSpace is str.isspace() for the code points latin-1 text can hold.
func pythonSpace(r rune) bool {
	switch {
	case r >= '\t' && r <= '\r', r >= 0x1c && r <= 0x20, r == 0x85, r == 0xa0:
		return true
	}
	return false
}

func pythonStrip(s string) string { return strings.TrimFunc(s, pythonSpace) }

func pythonSplit(s string) []string { return strings.FieldsFunc(s, pythonSpace) }

// pythonInt is int() of a bytes value: ASCII whitespace around, an optional
// sign, decimal digits with single underscores between them.
func pythonInt(b []byte) (int, bool) {
	s := strings.Trim(string(b), " \t\n\v\f\r")
	negative := false
	if s != "" && (s[0] == '+' || s[0] == '-') {
		negative = s[0] == '-'
		s = s[1:]
	}
	if s == "" || s[0] == '_' || s[len(s)-1] == '_' {
		return 0, false
	}
	value := 0
	for i := 0; i < len(s); i++ {
		switch c := s[i]; {
		case c >= '0' && c <= '9':
			value = value*10 + int(c-'0')
		case c == '_' && s[i-1] != '_':
		default:
			return 0, false
		}
	}
	if negative {
		value = -value
	}
	return value, true
}

const b64Alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

// pythonB64Decode is binascii.a2b_base64 in its default (non-strict) mode as
// Python 3.13.16 behaves: characters outside the alphabet and '=' are
// skipped while decoding; at the end an incomplete quad is accepted only when
// the '=' after the last data character complete it.
func pythonB64Decode(b []byte) ([]byte, error) {
	var out []byte
	quad, pads := 0, 0
	var left byte
	for _, c := range b {
		if c == '=' {
			pads++
			continue
		}
		index := strings.IndexByte(b64Alphabet, c)
		if index < 0 {
			continue
		}
		value := byte(index)
		pads = 0
		switch quad {
		case 0:
			quad, left = 1, value
		case 1:
			quad = 2
			out = append(out, left<<2|value>>4)
			left = value & 0x0f
		case 2:
			quad = 3
			out = append(out, left<<4|value>>2)
			left = value & 0x03
		default:
			quad = 0
			out = append(out, left<<6|value)
			left = 0
		}
	}
	if quad == 1 || quad != 0 && quad+pads < 4 {
		return nil, failure("binascii.Error")
	}
	return out, nil
}

// hasLineBreak reports a character str.splitlines() splits on.
func hasLineBreak(s string) bool {
	for _, r := range s {
		switch r {
		case '\n', '\r', '\v', '\f', 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029:
			return true
		}
	}
	return false
}
