package dto

import (
	"strconv"
	"strings"
)

// pythonJSONLoadsDepth is the container nesting json.loads reaches before it
// stops: the deepest run of open '[' and '{' the C scanner consumes before its
// first error, or before the end of the single top-level value (whatever
// follows is "Extra data" and is never scanned). It mirrors CPython 3.13's
// Modules/_json.c with strict=True: whitespace is only ' ', '\t', '\n' and
// '\r'; a leading UTF-8 BOM stops at once; values are strings, objects, arrays,
// null/true/false, NaN/Infinity/-Infinity and _match_number_unicode numbers (a
// '.' or exponent is consumed only with a digit after it, and an int past 4300
// digits stops); a string rejects a byte below 0x20 and every escape but
// " \ / b f n r t and \uXXXX. It is iterative (an explicit stack of container
// kinds) and linear, so 30000-level documents cannot overflow the Go stack. On
// any valid JSON document it equals auditJSONDepth's D.
func pythonJSONLoadsDepth(text string) int {
	if strings.HasPrefix(text, "\ufeff") {
		return 0
	}
	s, deepest := text, 0
	isDigit := func(c byte) bool { return c >= '0' && c <= '9' }
	hex4 := func(b string) bool { _, err := strconv.ParseUint(b, 16, 32); return err == nil }
	skipSpace := func() { s = strings.TrimLeft(s, " \t\n\r") }
	literal := func(word string) bool {
		if !strings.HasPrefix(s, word) {
			return false
		}
		s = s[len(word):]
		return true
	}
	// scanString consumes the string whose opening quote is s[0].
	scanString := func() bool {
		s = s[1:]
		for len(s) > 0 {
			switch c := s[0]; {
			case c == '"':
				s = s[1:]
				return true
			case c < 0x20:
				return false
			case c != '\\':
				s = s[1:] // any other byte, non-ASCII included
			case len(s) > 1 && strings.ContainsRune(`"\/bfnrt`, rune(s[1])):
				s = s[2:]
			case len(s) > 5 && s[1] == 'u' && hex4(s[2:6]):
				s = s[6:]
			default:
				return false
			}
		}
		return false
	}
	// scanNumber consumes _match_number_unicode at s; false stops.
	scanNumber := func() bool {
		rest := strings.TrimPrefix(s, "-")
		digits := 0
		if strings.HasPrefix(rest, "0") {
			rest, digits = rest[1:], 1
		} else {
			for digits < len(rest) && isDigit(rest[digits]) {
				digits++
			}
			if digits == 0 {
				return false
			}
			rest = rest[digits:]
		}
		isInt := true
		if len(rest) > 1 && rest[0] == '.' && isDigit(rest[1]) {
			isInt = false
			for rest = rest[2:]; len(rest) > 0 && isDigit(rest[0]); rest = rest[1:] {
			}
		}
		if len(rest) > 1 && (rest[0] == 'e' || rest[0] == 'E') {
			end := rest[1:]
			if len(end) > 0 && (end[0] == '+' || end[0] == '-') {
				end = end[1:]
			}
			if len(end) > 0 && isDigit(end[0]) {
				isInt = false
				for rest = end; len(rest) > 0 && isDigit(rest[0]); rest = rest[1:] {
				}
			}
		}
		s = rest
		return !isInt || digits <= 4300
	}
	// scanPrimitive consumes a literal, NaN/Infinity/-Infinity or a number.
	words := map[byte]string{'n': "null", 't': "true", 'f': "false", 'N': "NaN", 'I': "Infinity"}
	scanPrimitive := func(c byte) bool {
		if word, ok := words[c]; ok {
			return literal(word)
		}
		if c == '-' && literal("-Infinity") {
			return true
		}
		return scanNumber()
	}

	const (
		wantValue = iota
		wantAfterValue
		wantKey
		wantColon
	)
	var stack []byte
	for state := wantValue; ; {
		switch state {
		case wantValue:
			skipSpace()
			if s == "" {
				return deepest
			}
			switch c := s[0]; c {
			case '[', '{':
				s, stack = s[1:], append(stack, c)
				deepest = max(deepest, len(stack))
				skipSpace()
				if s != "" && s[0] == c+2 { // c+2 is ']' or '}'
					s, stack, state = s[1:], stack[:len(stack)-1], wantAfterValue
				} else if c == '{' {
					state = wantKey
				}
			case '"':
				if !scanString() {
					return deepest
				}
				state = wantAfterValue
			default:
				if !scanPrimitive(c) {
					return deepest
				}
				state = wantAfterValue
			}
		case wantAfterValue:
			skipSpace()
			if len(stack) == 0 || s == "" {
				return deepest
			}
			top, c := stack[len(stack)-1], s[0]
			switch {
			case c == ',':
				s = s[1:]
				skipSpace()
				if s == "" || top == '[' && s[0] == ']' || top == '{' && s[0] != '"' {
					return deepest // [1,] and {"a":1,} are trailing-comma errors
				}
				state = wantKey
				if top == '[' {
					state = wantValue
				}
			case c == top+2: // ']' for '[', '}' for '{'
				s, stack = s[1:], stack[:len(stack)-1]
			default:
				return deepest
			}
		case wantKey:
			if s == "" || s[0] != '"' || !scanString() {
				return deepest
			}
			state = wantColon
		case wantColon:
			skipSpace()
			if s == "" || s[0] != ':' {
				return deepest
			}
			s = s[1:]
			state = wantValue
		}
	}
}
