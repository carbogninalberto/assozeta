package dto

import (
	"encoding/json"
	"math/big"
	"strings"
	"unicode/utf8"
)

// PythonUUIDLookup is UUIDField.to_python for a stored JSON value: uuid.UUID(int=)
// for a JSON int or bool, uuid.UUID(hex=) with Python's int(text, 16) rules for a str.
// ok false is the ValidationError (a 500 in a view's own filter).
func PythonUUIDLookup(value any) (string, bool) {
	switch v := value.(type) {
	case json.Number, bool:
		return DjangoUUIDLookup(v)
	case string:
		return pythonUUIDFromHex(v)
	}
	return "", false
}

// pythonUUIDFromHex is uuid.UUID(hex=value): 'urn:' and 'uuid:' removed, the braces
// stripped and the dashes dropped, 32 code points, then int(text, 16).
func pythonUUIDFromHex(value string) (string, bool) {
	text := strings.ReplaceAll(value, "urn:", "")
	text = strings.ReplaceAll(text, "uuid:", "")
	text = strings.Trim(text, "{}")
	text = strings.ReplaceAll(text, "-", "")
	if utf8.RuneCountInString(text) != 32 {
		return "", false
	}
	number, ok := pythonHexInt(text)
	if !ok || number.BitLen() > 128 {
		return "", false
	}
	var raw [16]byte
	number.FillBytes(raw[:])
	const hexDigits = "0123456789abcdef"
	out := make([]byte, 0, 36)
	for index, b := range raw {
		if index == 4 || index == 6 || index == 8 || index == 10 {
			out = append(out, '-')
		}
		out = append(out, hexDigits[b>>4], hexDigits[b&15])
	}
	return string(out), true
}

// pythonHexInt is int(text, 16): non-ASCII whitespace and Unicode decimal digits
// are read as ' ' and ASCII digits, then ASCII whitespace, a single '+' and an
// optional '0x' prefix surround hex digits with single underscores between them.
func pythonHexInt(text string) (*big.Int, bool) {
	var normalised strings.Builder
	normalised.Grow(len(text))
	for _, r := range text {
		switch {
		case r < utf8.RuneSelf:
			normalised.WriteRune(r)
		case pythonIsSpace(r):
			normalised.WriteByte(' ')
		default:
			digit, ok := pythonDigit(r)
			if !ok {
				return nil, false
			}
			normalised.WriteByte(byte('0' + digit))
		}
	}
	ascii := strings.Trim(normalised.String(), " \t\n\v\f\r")
	if ascii != "" && ascii[0] == '+' {
		ascii = ascii[1:]
	}
	prefixed := len(ascii) >= 2 && ascii[0] == '0' && (ascii[1] == 'x' || ascii[1] == 'X')
	if prefixed {
		ascii = ascii[2:]
	}
	digits := make([]byte, 0, len(ascii))
	previousDigit := false
	leadingUnderscore := prefixed
	for index := 0; index < len(ascii); index++ {
		c := ascii[index]
		if c == '_' {
			if !previousDigit && !leadingUnderscore {
				return nil, false
			}
			leadingUnderscore = false
			previousDigit = false
			continue
		}
		var digit byte
		switch {
		case c >= '0' && c <= '9':
			digit = c - '0'
		case c >= 'a' && c <= 'f':
			digit = c - 'a' + 10
		case c >= 'A' && c <= 'F':
			digit = c - 'A' + 10
		default:
			return nil, false
		}
		leadingUnderscore = false
		previousDigit = true
		digits = append(digits, "0123456789abcdef"[digit])
	}
	if !previousDigit {
		return nil, false
	}
	number, ok := new(big.Int).SetString(string(digits), 16)
	if !ok {
		return nil, false
	}
	return number, true
}
