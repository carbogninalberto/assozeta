package service

import (
	"context"
	"encoding/json"
	"sort"
	"strconv"
	"strings"
	"unicode/utf16"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

func profileBoolean(value any) (bool, error) {
	switch v := value.(type) {
	case bool:
		return v, nil
	case string:
		switch v {
		case "t", "True", "1":
			return true, nil
		case "f", "False", "0":
			return false, nil
		}
	case json.Number:
		if json.Valid([]byte(v)) {
			if !strings.ContainsAny(string(v), ".eE") {
				if v == "1" {
					return true, nil
				}
				if v == "0" || v == "-0" {
					return false, nil
				}
			} else if number, err := strconv.ParseFloat(string(v), 64); err == nil || number == 0 {
				if number == 0 {
					return false, nil
				}
				if number == 1 {
					return true, nil
				}
			}
		}
	}
	return false, dto.ErrProfileUpdateShape
}

// Auditlog stringifies JSON fields with Python's json.dumps(sort_keys=True).
// Preserve its spaces, ASCII escapes, float/int distinction and number spelling.
// The same valid JSON text can be bound to the existing jsonb business column.
func profileJSONText(ctx context.Context, value any) (string, error) {
	var out strings.Builder
	writeHex := func(r rune) {
		digits := strconv.FormatInt(int64(r), 16)
		out.WriteString(`\u`)
		out.WriteString(strings.Repeat("0", 4-len(digits)))
		out.WriteString(digits)
	}
	quote := func(value string) error {
		if !utf8.ValidString(value) {
			return ErrLoginValue
		}
		out.WriteByte('"')
		processed := 0
		for _, r := range value {
			processed++
			if processed%1024 == 0 {
				if err := ctx.Err(); err != nil {
					return err
				}
			}
			switch r {
			case '"':
				out.WriteString(`\"`)
			case '\\':
				out.WriteString(`\\`)
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
				if r > 0xffff {
					high, low := utf16.EncodeRune(r)
					writeHex(high)
					writeHex(low)
				} else if r < 0x20 || r >= 0x7f {
					writeHex(r)
				} else {
					out.WriteRune(r)
				}
			}
		}
		out.WriteByte('"')
		return nil
	}
	var appendValue func(any, int) error
	appendValue = func(v any, depth int) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if depth > 1000 {
			return ErrLoginValue
		}
		switch x := v.(type) {
		case nil:
			out.WriteString("null")
		case bool:
			out.WriteString(strconv.FormatBool(x))
		case string:
			return quote(x)
		case json.Number:
			text, err := loginValueText(ctx, x)
			if err != nil {
				return err
			}
			if !json.Valid([]byte(text)) {
				return ErrLoginValue
			}
			out.WriteString(text)
		case []any:
			out.WriteByte('[')
			for i, item := range x {
				if i > 0 {
					out.WriteString(", ")
				}
				if err := appendValue(item, depth+1); err != nil {
					return err
				}
			}
			out.WriteByte(']')
		case domain.RequestObject:
			fields := make(map[string]any, len(x))
			for _, field := range x {
				fields[field.Name] = field.Value
			}
			return appendValue(fields, depth)
		case map[string]any:
			keys := make([]string, 0, len(x))
			for key := range x {
				keys = append(keys, key)
			}
			sort.Strings(keys)
			out.WriteByte('{')
			for i, key := range keys {
				if i > 0 {
					out.WriteString(", ")
				}
				if err := quote(key); err != nil {
					return err
				}
				out.WriteString(": ")
				if err := appendValue(x[key], depth+1); err != nil {
					return err
				}
			}
			out.WriteByte('}')
		default:
			return ErrLoginValue
		}
		return nil
	}
	if err := appendValue(value, 0); err != nil {
		return "", err
	}
	if err := ctx.Err(); err != nil {
		return "", err
	}
	return out.String(), nil
}
