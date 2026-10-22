package dto

import (
	"bytes"
	"encoding/json"
	"strconv"
	"unicode/utf16"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// Python retains unpaired UTF-16 escapes; PostgreSQL then rejects the JSONField
// write. encoding/json would silently replace them with U+FFFD. Keep their
// invalid UTF-8 code-unit encoding for this endpoint so the service rejects the
// write, while literal U+FFFD, valid pairs and ignored fields remain distinct.
func ParseProfileTablesJSON(data []byte) (any, error) {
	constants := map[int64]any{}
	for i := 0; i < len(data); {
		if data[i] != '"' {
			i++
			continue
		}
		start := i
		i++
		for i < len(data) {
			if data[i] == '\\' {
				i += 2
				continue
			}
			if data[i] == '"' {
				i++
				break
			}
			i++
		}
		if i > len(data) {
			break
		}
		raw := data[start:i]
		if !bytes.Contains(raw, []byte(`\u`)) {
			continue
		}
		value, invalid, err := tableJSONCodeUnits(raw)
		if err != nil {
			return nil, err
		}
		if invalid {
			constants[int64(i)] = value
		}
	}
	return parseReferenceJSON(data, constants, false)
}

func tableJSONCodeUnits(raw []byte) (string, bool, error) {
	if !json.Valid(raw) {
		return "", false, domain.ErrInvalidScopedRequest
	}
	out := make([]byte, 0, len(raw))
	invalid := false
	for i := 1; i < len(raw)-1; {
		if raw[i] != '\\' {
			out = append(out, raw[i])
			i++
			continue
		}
		i++
		escape := raw[i]
		i++
		if escape != 'u' {
			switch escape {
			case 'b':
				escape = '\b'
			case 'f':
				escape = '\f'
			case 'n':
				escape = '\n'
			case 'r':
				escape = '\r'
			case 't':
				escape = '\t'
			}
			out = append(out, escape)
			continue
		}
		value, _ := strconv.ParseUint(string(raw[i:i+4]), 16, 16)
		i += 4
		r := rune(value)
		if r >= 0xd800 && r <= 0xdbff && i+6 <= len(raw)-1 && raw[i] == '\\' && raw[i+1] == 'u' {
			low, _ := strconv.ParseUint(string(raw[i+2:i+6]), 16, 16)
			if low >= 0xdc00 && low <= 0xdfff {
				r = utf16.DecodeRune(r, rune(low))
				i += 6
			}
		}
		if r >= 0xd800 && r <= 0xdfff {
			invalid = true
			out = append(out, byte(0xe0|(r>>12)), byte(0x80|((r>>6)&0x3f)), byte(0x80|(r&0x3f)))
		} else {
			out = utf8.AppendRune(out, r)
		}
	}
	return string(out), invalid, nil
}
