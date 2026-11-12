package associationexport

import (
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
)

// Item is one key of a Python dict (insertion order kept).
type Item struct {
	Key   string
	Value any
}

// Dict is a Python dict.
type Dict []Item

// Int is a Python int given by its decimal text.
type Int string

// Float is a Python float.
type Float float64

// Raw is already-encoded JSON text.
type Raw string

// AppendJSON is json.dump(value, ensure_ascii=False) with the default
// separators (', ' and ': '): strings keep non-ASCII characters, floats use
// repr(), and Infinity/NaN are written as Python does (allow_nan).
func AppendJSON(b []byte, value any) ([]byte, error) {
	switch v := value.(type) {
	case nil:
		return append(b, "null"...), nil
	case bool:
		if v {
			return append(b, "true"...), nil
		}
		return append(b, "false"...), nil
	case int:
		return strconv.AppendInt(b, int64(v), 10), nil
	case int64:
		return strconv.AppendInt(b, v, 10), nil
	case Int:
		return append(b, v...), nil
	case Float:
		return append(b, pythonFloatJSON(float64(v))...), nil
	case Raw:
		return append(b, v...), nil
	case string:
		return appendPythonString(b, v), nil
	case []any:
		b = append(b, '[')
		for i, item := range v {
			if i > 0 {
				b = append(b, ", "...)
			}
			var err error
			if b, err = AppendJSON(b, item); err != nil {
				return nil, err
			}
		}
		return append(b, ']'), nil
	case []string:
		b = append(b, '[')
		for i, item := range v {
			if i > 0 {
				b = append(b, ", "...)
			}
			b = appendPythonString(b, item)
		}
		return append(b, ']'), nil
	case Dict:
		b = append(b, '{')
		for i, item := range v {
			if i > 0 {
				b = append(b, ", "...)
			}
			b = appendPythonString(b, item.Key)
			b = append(b, ": "...)
			var err error
			if b, err = AppendJSON(b, item.Value); err != nil {
				return nil, err
			}
		}
		return append(b, '}'), nil
	}
	return nil, fmt.Errorf("associationexport: unsupported JSON value %T", value)
}

// appendPythonString is json's py_encode_basestring (ensure_ascii=False).
func appendPythonString(b []byte, s string) []byte {
	b = append(b, '"')
	for i := 0; i < len(s); i++ {
		c := s[i]
		switch c {
		case '\\':
			b = append(b, `\\`...)
		case '"':
			b = append(b, `\"`...)
		case '\n':
			b = append(b, `\n`...)
		case '\r':
			b = append(b, `\r`...)
		case '\t':
			b = append(b, `\t`...)
		case '\b':
			b = append(b, `\b`...)
		case '\f':
			b = append(b, `\f`...)
		default:
			if c < 0x20 {
				b = append(b, fmt.Sprintf(`\u%04x`, c)...)
			} else {
				b = append(b, c)
			}
		}
	}
	return append(b, '"')
}

// pythonFloatJSON is float.__repr__ (and json's Infinity/NaN).
func pythonFloatJSON(f float64) string {
	switch {
	case math.IsNaN(f):
		return "NaN"
	case math.IsInf(f, 1):
		return "Infinity"
	case math.IsInf(f, -1):
		return "-Infinity"
	}
	return PythonFloatRepr(f)
}

// PythonFloatRepr is repr(float) for finite values: the shortest round-trip
// digits, fixed notation for decimal exponents in [-4, 16), else scientific
// with a signed exponent of at least two digits.
func PythonFloatRepr(f float64) string {
	if f == 0 {
		if math.Signbit(f) {
			return "-0.0"
		}
		return "0.0"
	}
	text := strconv.FormatFloat(f, 'e', -1, 64)
	sign := ""
	if text[0] == '-' {
		sign, text = "-", text[1:]
	}
	mantissa, exponentText, _ := strings.Cut(text, "e")
	exponent, _ := strconv.Atoi(exponentText)
	digits := strings.Replace(mantissa, ".", "", 1)
	// value = 0.digits * 10^(exponent+1)
	point := exponent + 1
	if -4 < point && point <= 16 {
		switch {
		case point <= 0:
			return sign + "0." + strings.Repeat("0", -point) + digits
		case point >= len(digits):
			return sign + digits + strings.Repeat("0", point-len(digits)) + ".0"
		default:
			return sign + digits[:point] + "." + digits[point:]
		}
	}
	out := digits[:1]
	if len(digits) > 1 {
		out += "." + digits[1:]
	}
	exp := point - 1
	expSign := "+"
	if exp < 0 {
		expSign, exp = "-", -exp
	}
	expText := strconv.Itoa(exp)
	if len(expText) < 2 {
		expText = "0" + expText
	}
	return sign + out + "e" + expSign + expText
}

// FromJSONB converts the jsonb text PostgreSQL returns into the value
// json.loads builds (dict order kept; integers as Python ints, other numbers
// as floats).
func FromJSONB(text string) (any, error) {
	value, err := celery.DecodeJSON([]byte(text))
	if err != nil {
		return nil, err
	}
	return convertJSON(value)
}

func convertJSON(value any) (any, error) {
	switch v := value.(type) {
	case nil, bool, string:
		return v, nil
	case celery.Number:
		return pythonNumber(string(v))
	case []any:
		out := make([]any, len(v))
		for i, item := range v {
			converted, err := convertJSON(item)
			if err != nil {
				return nil, err
			}
			out[i] = converted
		}
		return out, nil
	case celery.Dict:
		out := make(Dict, 0, len(v))
		for _, item := range v {
			converted, err := convertJSON(item.Value)
			if err != nil {
				return nil, err
			}
			// json.loads keeps the last value of a duplicated key at the first
			// key's position (jsonb never has duplicates).
			out = append(out, Item{Key: item.Key, Value: converted})
		}
		return out, nil
	}
	return nil, fmt.Errorf("associationexport: unsupported jsonb value %T", value)
}

// pythonNumber is json.loads' number parsing: int for an integer literal,
// float otherwise.
func pythonNumber(text string) (any, error) {
	if !strings.ContainsAny(text, ".eE") {
		n, ok := new(big.Int).SetString(text, 10)
		if !ok {
			return nil, fmt.Errorf("associationexport: number %q", text)
		}
		return Int(n.String()), nil
	}
	f, err := strconv.ParseFloat(text, 64)
	if err != nil && !math.IsInf(f, 0) {
		return nil, fmt.Errorf("associationexport: number %q", text)
	}
	return Float(f), nil
}
