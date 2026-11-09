package dto

import (
	"encoding/json"
	"math/big"
	"strconv"
	"strings"
	"unicode/utf8"
)

// AuditIndexUUIDText is uuid.UUID(hex=text) as UUIDField.to_python applies
// it to a str: "urn:" and "uuid:" removed, braces stripped from both ends,
// hyphens removed, exactly 32 characters, then int(text, 16) (surrounding
// whitespace, a "+" sign, an optional 0x prefix, single underscores between
// digits and Unicode decimal digits). The result is the canonical form.
func AuditIndexUUIDText(text string) (string, bool) {
	text = strings.ReplaceAll(strings.ReplaceAll(text, "urn:", ""), "uuid:", "")
	text = strings.ReplaceAll(strings.Trim(text, "{}"), "-", "")
	if utf8.RuneCountInString(text) != 32 {
		return "", false
	}
	value, ok := auditIndexHexInt(text)
	if !ok {
		return "", false
	}
	return auditIndexUUIDFromInt(value)
}

// auditIndexHexInt is Python's int(text, 16).
func auditIndexHexInt(text string) (*big.Int, bool) {
	text = PythonStrip(text)
	if text != "" && (text[0] == '+' || text[0] == '-') {
		if text[0] == '-' {
			return nil, false // a UUID's hyphens are removed before int()
		}
		text = text[1:]
	}
	afterPrefix := false
	if len(text) >= 2 && text[0] == '0' && (text[1] == 'x' || text[1] == 'X') {
		text = text[2:]
		afterPrefix = true
	}
	var digits strings.Builder
	previousDigit := false
	for _, r := range text {
		if r == '_' {
			if !previousDigit && !(afterPrefix && digits.Len() == 0) {
				return nil, false
			}
			previousDigit, afterPrefix = false, false
			continue
		}
		afterPrefix = false
		switch {
		case r >= 'a' && r <= 'f', r >= 'A' && r <= 'F':
			digits.WriteRune(r)
		default:
			digit, ok := pythonDigit(r)
			if !ok {
				return nil, false
			}
			digits.WriteByte(byte('0' + digit))
		}
		previousDigit = true
	}
	if digits.Len() == 0 || !previousDigit {
		return nil, false
	}
	value, ok := new(big.Int).SetString(digits.String(), 16)
	return value, ok
}

func auditIndexUUIDFromInt(value *big.Int) (string, bool) {
	if value.Sign() < 0 || value.BitLen() > 128 {
		return "", false
	}
	var raw [16]byte
	value.FillBytes(raw[:])
	const hex = "0123456789abcdef"
	out := make([]byte, 0, 36)
	for index, b := range raw {
		if index == 4 || index == 6 || index == 8 || index == 10 {
			out = append(out, '-')
		}
		out = append(out, hex[b>>4], hex[b&15])
	}
	return string(out), true
}

// AuditIndexUUIDValue is UUIDField.to_python for a value decoded from JSON
// (numbers as json.Number): an int (or bool) is uuid.UUID(int=...), a str is
// uuid.UUID(hex=...); floats, lists, objects and out-of-range ints fail.
func AuditIndexUUIDValue(value any) (string, bool) {
	switch v := value.(type) {
	case bool:
		if v {
			return auditIndexUUIDFromInt(big.NewInt(1))
		}
		return auditIndexUUIDFromInt(big.NewInt(0))
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return "", false
		}
		number, ok := new(big.Int).SetString(string(v), 10)
		if !ok {
			return "", false
		}
		return auditIndexUUIDFromInt(number)
	case string:
		return AuditIndexUUIDText(v)
	}
	return "", false
}

// AuditIndexTruthy is Python truthiness of a JSON-decoded value.
func AuditIndexTruthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			number, _ := strconv.ParseFloat(string(v), 64)
			return number != 0
		}
		number, ok := new(big.Int).SetString(string(v), 10)
		return !ok || number.Sign() != 0
	case []any:
		return len(v) > 0
	case map[string]any:
		return len(v) > 0
	}
	return true
}

// AuditIndexAutoPK is AutoField.get_prep_value for an object_pk string:
// Python int(text); ok false is the lookup error. inRange false is a value
// outside the integer column, which Django 5 matches against nothing.
func AuditIndexAutoPK(text string) (value int64, inRange bool, ok bool) {
	number, ok := PythonIntText(text)
	if !ok {
		return 0, false, false
	}
	if !number.IsInt64() || number.Int64() < -1<<31 || number.Int64() > 1<<31-1 {
		return 0, false, true
	}
	return number.Int64(), true, true
}
