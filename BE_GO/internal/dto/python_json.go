package dto

import (
	"encoding/json"
	"math"
	"math/big"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// PythonJSONDumps reproduces Python's json.dumps defaults for parsed reference
// values: ASCII escaping, ", " and ": " separators, insertion order, integer
// precision and float repr. Strings may carry the lone surrogate code units
// preserved by ParseProfileTablesJSON; they are emitted as \udXXX escapes.
func PythonJSONDumps(value any) []byte {
	var out strings.Builder
	pythonJSONWrite(&out, value)
	return []byte(out.String())
}

func pythonJSONWrite(out *strings.Builder, value any) {
	switch v := value.(type) {
	case nil:
		out.WriteString("null")
	case bool:
		if v {
			out.WriteString("true")
		} else {
			out.WriteString("false")
		}
	case string:
		pythonJSONString(out, v)
	case json.Number:
		out.WriteString(pythonJSONNumber(string(v)))
	case domain.RequestObject:
		out.WriteByte('{')
		for i, field := range v {
			if i > 0 {
				out.WriteString(", ")
			}
			pythonJSONString(out, field.Name)
			out.WriteString(": ")
			pythonJSONWrite(out, field.Value)
		}
		out.WriteByte('}')
	case []any:
		out.WriteByte('[')
		for i, item := range v {
			if i > 0 {
				out.WriteString(", ")
			}
			pythonJSONWrite(out, item)
		}
		out.WriteByte(']')
	default:
		panic("unsupported reference JSON value")
	}
}

func pythonJSONString(out *strings.Builder, value string) {
	const hex = "0123456789abcdef"
	unit := func(r rune) {
		out.WriteString(`\u`)
		out.WriteByte(hex[r>>12&0xf])
		out.WriteByte(hex[r>>8&0xf])
		out.WriteByte(hex[r>>4&0xf])
		out.WriteByte(hex[r&0xf])
	}
	out.WriteByte('"')
	for _, r := range pythonCodePoints(value) {
		switch {
		case r == '"':
			out.WriteString(`\"`)
		case r == '\\':
			out.WriteString(`\\`)
		case r == '\n':
			out.WriteString(`\n`)
		case r == '\r':
			out.WriteString(`\r`)
		case r == '\t':
			out.WriteString(`\t`)
		case r == '\b':
			out.WriteString(`\b`)
		case r == '\f':
			out.WriteString(`\f`)
		case r >= ' ' && r <= '~':
			out.WriteRune(r)
		case r > 0xffff:
			r -= 0x10000
			unit(0xd800 | r>>10)
			unit(0xdc00 | r&0x3ff)
		default:
			unit(r)
		}
	}
	out.WriteByte('"')
}

// pythonCodePoints decodes WTF-8, treating each preserved surrogate code unit
// as one Python character.
func pythonCodePoints(value string) []rune {
	result := make([]rune, 0, len(value))
	for len(value) > 0 {
		if len(value) >= 3 && value[0] == 0xed && value[1] >= 0xa0 && value[1] <= 0xbf && value[2] >= 0x80 && value[2] <= 0xbf {
			result = append(result, rune(value[0]&0x0f)<<12|rune(value[1]&0x3f)<<6|rune(value[2]&0x3f))
			value = value[3:]
			continue
		}
		r, size := utf8.DecodeRuneInString(value)
		result = append(result, r)
		value = value[size:]
	}
	return result
}

func pythonJSONNumber(token string) string {
	if !strings.ContainsAny(token, ".eE") {
		integer, ok := new(big.Int).SetString(token, 10)
		if !ok {
			panic("invalid integer token")
		}
		return integer.String()
	}
	// Overflow yields +/-Inf, which Python also produces before encoding.
	value, _ := strconv.ParseFloat(token, 64)
	return pythonFloatRepr(value)
}

// pythonFloatRepr is Python's shortest round-trip repr: positional notation
// for decimal exponents -4..15, otherwise d[.ddd]e+XX with two exponent digits.
func pythonFloatRepr(value float64) string {
	switch {
	case math.IsInf(value, 1):
		return "Infinity"
	case math.IsInf(value, -1):
		return "-Infinity"
	case math.IsNaN(value):
		return "NaN"
	case value == 0:
		if math.Signbit(value) {
			return "-0.0"
		}
		return "0.0"
	}
	text := strconv.FormatFloat(value, 'e', -1, 64)
	sign := ""
	if text[0] == '-' {
		sign, text = "-", text[1:]
	}
	mantissa, exponentText, _ := strings.Cut(text, "e")
	digits := strings.Replace(mantissa, ".", "", 1)
	exponent, _ := strconv.Atoi(exponentText)
	if exponent < -4 || exponent > 15 {
		result := digits[:1]
		if len(digits) > 1 {
			result += "." + digits[1:]
		}
		mark := "+"
		if exponent < 0 {
			mark, exponent = "-", -exponent
		}
		return sign + result + "e" + mark + leftPad(strconv.Itoa(exponent), 2)
	}
	point := exponent + 1
	switch {
	case point <= 0:
		return sign + "0." + strings.Repeat("0", -point) + digits
	case point >= len(digits):
		return sign + digits + strings.Repeat("0", point-len(digits)) + ".0"
	default:
		return sign + digits[:point] + "." + digits[point:]
	}
}

func leftPad(value string, width int) string {
	if len(value) >= width {
		return value
	}
	return strings.Repeat("0", width-len(value)) + value
}
