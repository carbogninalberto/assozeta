package manual

import (
	"bytes"
	"encoding/json"
	"io"
	"math"
	"math/big"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

// A seal is SHA256 of Python's sorted, compact, ensure_ascii=False JSON.
// Preserve arbitrary precision integers and Python float spelling; Go's ordinary
// JSON marshaler changes exponent thresholds, integral floats and HTML escaping.
func decodeJSON(raw []byte) (any, error) {
	if !utf8.Valid(raw) {
		return nil, errEvidence
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	var value any
	if err := d.Decode(&value); err != nil {
		return nil, errEvidence
	}
	if err := d.Decode(new(any)); err != io.EOF {
		return nil, errEvidence
	}
	return value, nil
}

// encoding/json replaces unpaired UTF-16 escapes with U+FFFD. Python's
// ensure_ascii=False seal cannot encode those surrogate codepoints, so never
// accept a seal of the replacement text as if it authenticated the original.
func decodeSealedJSON(raw []byte) (any, error) {
	for i := 0; i < len(raw); i++ {
		if raw[i] != '\\' {
			continue
		}
		i++
		if i >= len(raw) || raw[i] != 'u' {
			continue
		}
		if i+4 >= len(raw) {
			return nil, errEvidence
		}
		n, err := strconv.ParseUint(string(raw[i+1:i+5]), 16, 16)
		if err != nil {
			return nil, errEvidence
		}
		i += 4
		if n >= 0xdc00 && n <= 0xdfff {
			return nil, errEvidence
		}
		if n >= 0xd800 && n <= 0xdbff {
			if i+6 >= len(raw) || raw[i+1] != '\\' || raw[i+2] != 'u' {
				return nil, errEvidence
			}
			low, err := strconv.ParseUint(string(raw[i+3:i+7]), 16, 16)
			if err != nil || low < 0xdc00 || low > 0xdfff {
				return nil, errEvidence
			}
			i += 6
		}
	}
	return decodeJSON(raw)
}
func canonical(value any) ([]byte, error) {
	var out strings.Builder
	var write func(any) error
	quote := func(value string) {
		out.WriteByte('"')
		const hex = "0123456789abcdef"
		for _, r := range value {
			switch r {
			case '"', '\\':
				out.WriteByte('\\')
				out.WriteRune(r)
			case '\b':
				out.WriteString(`\b`)
			case '\f':
				out.WriteString(`\f`)
			case '\n':
				out.WriteString(`\n`)
			case '\r':
				out.WriteString(`\r`)
			case '\t':
				out.WriteString(`\t`)
			default:
				if r < 32 {
					out.WriteString(`\u00`)
					out.WriteByte(hex[r>>4])
					out.WriteByte(hex[r&15])
				} else {
					out.WriteRune(r)
				}
			}
		}
		out.WriteByte('"')
	}
	write = func(value any) error {
		switch v := value.(type) {
		case nil:
			out.WriteString("null")
		case bool:
			out.WriteString(strconv.FormatBool(v))
		case string:
			quote(v)
		case json.Number:
			raw := string(v)
			if !strings.ContainsAny(raw, ".eE") {
				if len(strings.TrimPrefix(raw, "-")) > 4300 {
					return errEvidence
				}
				n, ok := new(big.Int).SetString(raw, 10)
				if !ok {
					return errEvidence
				}
				out.WriteString(n.String())
				break
			}
			n, err := strconv.ParseFloat(raw, 64)
			if err != nil || math.IsInf(n, 0) || math.IsNaN(n) {
				return errEvidence
			}
			format := byte('f')
			if a := math.Abs(n); a != 0 && (a < 1e-4 || a >= 1e16) {
				format = 'e'
			}
			raw = strconv.FormatFloat(n, format, -1, 64)
			if format == 'f' && !strings.Contains(raw, ".") {
				raw += ".0"
			}
			out.WriteString(raw)
		case []any:
			out.WriteByte('[')
			for i, item := range v {
				if i > 0 {
					out.WriteByte(',')
				}
				if err := write(item); err != nil {
					return err
				}
			}
			out.WriteByte(']')
		case map[string]any:
			keys := make([]string, 0, len(v))
			for k := range v {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			out.WriteByte('{')
			for i, k := range keys {
				if i > 0 {
					out.WriteByte(',')
				}
				quote(k)
				out.WriteByte(':')
				if err := write(v[k]); err != nil {
					return err
				}
			}
			out.WriteByte('}')
		default:
			return errEvidence
		}
		return nil
	}
	if err := write(value); err != nil {
		return nil, err
	}
	return []byte(out.String()), nil
}
