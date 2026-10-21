package service

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

var ErrLoginValue = errors.New("invalid login value representation")

// loginValueText reproduces str() for transport values. Containers use repr()
// for their children, preserving dictionary order and exact JSON integer digits.
// The transport bounds input bytes; output is linear in input size (at most six
// bytes per input byte plus container separators). Nesting matches the JSON parser.
func loginValueText(ctx context.Context, value any) (string, error) {
	var out strings.Builder
	var appendValue func(any, bool, int) error
	quote := func(s string) error {
		if !utf8.ValidString(s) {
			return ErrLoginValue
		}
		q := byte('\'')
		if strings.Contains(s, "'") && !strings.Contains(s, "\"") {
			q = '"'
		}
		out.WriteByte(q)
		processed := 0
		for _, r := range s {
			processed++
			if processed%1024 == 0 {
				if err := ctx.Err(); err != nil {
					return err
				}
			}
			switch r {
			case '\\':
				out.WriteString(`\\`)
			case '\t':
				out.WriteString(`\t`)
			case '\n':
				out.WriteString(`\n`)
			case '\r':
				out.WriteString(`\r`)
			default:
				if r == rune(q) {
					out.WriteByte('\\')
					out.WriteRune(r)
				} else if unicode.IsPrint(r) {
					out.WriteRune(r)
				} else {
					width, prefix := 2, `\x`
					if r > 0xff {
						width, prefix = 4, `\u`
					}
					if r > 0xffff {
						width, prefix = 8, `\U`
					}
					digits := strconv.FormatInt(int64(r), 16)
					out.WriteString(prefix)
					out.WriteString(strings.Repeat("0", width-len(digits)))
					out.WriteString(digits)
				}
			}
		}
		out.WriteByte(q)
		return nil
	}
	appendValue = func(v any, nested bool, depth int) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		if depth > 1000 {
			return ErrLoginValue
		}
		switch x := v.(type) {
		case nil:
			out.WriteString("None")
		case bool:
			if x {
				out.WriteString("True")
			} else {
				out.WriteString("False")
			}
		case string:
			if nested {
				return quote(x)
			}
			if !utf8.ValidString(x) {
				return ErrLoginValue
			}
			out.WriteString(x)
		case domain.RequestUpload:
			if nested {
				return ErrLoginValue
			}
			out.WriteString(x.Name)
		case json.Number:
			raw := string(x)
			if len(raw) == 0 || (raw[0] != '-' && (raw[0] < '0' || raw[0] > '9')) || !json.Valid([]byte(raw)) {
				return ErrLoginValue
			}
			if !strings.ContainsAny(raw, ".eE") {
				digits := strings.TrimPrefix(raw, "-")
				if len(digits) > 4300 {
					return ErrLoginValue
				}
				if raw == "-0" {
					raw = "0"
				}
				out.WriteString(raw)
				break
			}
			f, err := strconv.ParseFloat(raw, 64)
			if err != nil && !errors.Is(err, strconv.ErrRange) {
				return ErrLoginValue
			}
			if math.IsInf(f, 1) {
				out.WriteString("inf")
			} else if math.IsInf(f, -1) {
				out.WriteString("-inf")
			} else if a := math.Abs(f); a != 0 && (a < 1e-4 || a >= 1e16) {
				out.WriteString(strconv.FormatFloat(f, 'e', -1, 64))
			} else {
				text := strconv.FormatFloat(f, 'f', -1, 64)
				if !strings.Contains(text, ".") {
					text += ".0"
				}
				out.WriteString(text)
			}
		case []any:
			out.WriteByte('[')
			for i, item := range x {
				if i > 0 {
					out.WriteString(", ")
				}
				if err := appendValue(item, true, depth+1); err != nil {
					return err
				}
			}
			out.WriteByte(']')
		case domain.RequestObject:
			out.WriteByte('{')
			for i, field := range x {
				if i > 0 {
					out.WriteString(", ")
				}
				if err := quote(field.Name); err != nil {
					return err
				}
				out.WriteString(": ")
				if err := appendValue(field.Value, true, depth+1); err != nil {
					return err
				}
			}
			out.WriteByte('}')
		default:
			return ErrLoginValue
		}
		return nil
	}
	if err := appendValue(value, false, 0); err != nil {
		return "", err
	}
	if err := ctx.Err(); err != nil {
		return "", err
	}
	return out.String(), nil
}
