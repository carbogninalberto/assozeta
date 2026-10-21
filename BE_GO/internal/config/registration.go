package config

import (
	"errors"
	"strings"
	"unicode"
)

// Match django-environ Env.bool, including integer strings before word values.
// A proxy inherits the originating variable's default. Cycles fail startup.
func registrationFlag(values map[string]string, name string, fallback bool) (bool, error) {
	seen := map[string]bool{}
	for {
		if seen[name] {
			return false, errors.New("cyclic registration flag proxy")
		}
		seen[name] = true
		value, ok := values[name]
		if !ok {
			return fallback, nil
		}
		if strings.HasPrefix(value, "$") {
			name = strings.TrimLeft(value, "$")
			continue
		}
		return djangoBoolean(value), nil
	}
}
func djangoBoolean(value string) bool {
	number := strings.TrimFunc(value, unicode.IsSpace)
	if strings.HasPrefix(number, "+") || strings.HasPrefix(number, "-") {
		number = number[1:]
	}
	valid, previousDigit, nonzero, digits := number != "", false, false, 0
	for _, r := range number {
		if r == '_' {
			if !previousDigit {
				valid = false
				break
			}
			previousDigit = false
			continue
		}
		digit := -1
		for _, zero := range pythonDecimalZeros {
			if r >= zero && r <= zero+9 {
				digit = int(r - zero)
				break
			}
		}
		if digit < 0 {
			valid = false
			break
		}
		digits++
		previousDigit = true
		nonzero = nonzero || digit != 0
	}
	if valid && previousDigit && digits <= 4300 {
		return nonzero
	}
	word := strings.TrimFunc(strings.ToLower(value), func(r rune) bool { return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f) })
	switch word {
	case "true", "on", "ok", "y", "yes", "1":
		return true
	}
	return false
}

// Unicode 15.1.0 decimal-zero blocks, matching shipped Python 3.13.2.
var pythonDecimalZeros = [...]rune{0x30, 0x660, 0x6f0, 0x7c0, 0x966, 0x9e6, 0xa66, 0xae6, 0xb66, 0xbe6, 0xc66, 0xce6, 0xd66, 0xde6, 0xe50, 0xed0, 0xf20, 0x1040, 0x1090, 0x17e0, 0x1810, 0x1946, 0x19d0, 0x1a80, 0x1a90, 0x1b50, 0x1bb0, 0x1c40, 0x1c50, 0xa620, 0xa8d0, 0xa900, 0xa9d0, 0xa9f0, 0xaa50, 0xabf0, 0xff10, 0x104a0, 0x10d30, 0x11066, 0x110f0, 0x11136, 0x111d0, 0x112f0, 0x11450, 0x114d0, 0x11650, 0x116c0, 0x11730, 0x118e0, 0x11950, 0x11c50, 0x11d50, 0x11da0, 0x11f50, 0x16a60, 0x16ac0, 0x16b50, 0x1d7ce, 0x1d7d8, 0x1d7e2, 0x1d7ec, 0x1d7f6, 0x1e140, 0x1e2f0, 0x1e4f0, 0x1e950, 0x1fbf0}

// read_env defaults to literal comments, exact KEY= assignments and line-based
// quote removal. Values are never logged; process environment overrides later.
func djangoEnvironmentFile(content string) map[string]string {
	values := map[string]string{}
	lines := strings.FieldsFunc(content, func(r rune) bool {
		return r == '\n' || r == '\r' || r == '\v' || r == '\f' || (r >= 0x1c && r <= 0x1e) || r == 0x85 || r == 0x2028 || r == 0x2029
	})
	for _, line := range lines {
		line = strings.TrimPrefix(line, "export ")
		key, value, ok := strings.Cut(line, "=")
		if !ok || key == "" {
			continue
		}
		valid := true
		for _, r := range key {
			if !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == '_') {
				valid = false
				break
			}
		}
		if !valid {
			continue
		}
		if len(value) >= 2 && value[0] == '\'' && value[len(value)-1] == '\'' {
			value = value[1 : len(value)-1]
		}
		if len(value) >= 2 && value[0] == '"' && value[len(value)-1] == '"' {
			runes := []rune(value[1 : len(value)-1])
			var out strings.Builder
			for i := 0; i < len(runes); i++ {
				r := runes[i]
				if r == '\\' && i+1 < len(runes) {
					i++
					next := runes[i]
					if next == 'n' || next == 'r' || next == 't' {
						out.WriteRune('\\')
					}
					out.WriteRune(next)
				} else {
					out.WriteRune(r)
				}
			}
			value = out.String()
		}
		values[key] = value
	}
	return values
}
