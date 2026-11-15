package dto

import (
	"bytes"
	"encoding/binary"
	"errors"
	"math"
	"strings"
	"unicode/utf16"
	"unicode/utf8"
)

// runnerJSONDepth is the deepest nesting the decoder reads.
const runnerJSONDepth = 500

// RenderableJSON reports whether DRF's JSONRenderer writes value: the strict
// encoder raises ValueError on a non-finite float and the UTF-8 encode fails on
// a lone surrogate (see AuditResponseEncodable).
func RenderableJSON(value any) bool { return AuditResponseEncodable(value) }

// RunnerJSONDecode is json.loads(response.read()) as updater_client's calls
// read a reply from the update runner.
//
// The bytes are decoded with json's detect_encoding (the UTF-8, UTF-16 and
// UTF-32 BOMs and the NUL pattern that selects utf-16-be/le and utf-32-be/le)
// and the surrogatepass handler, so a code unit that Python keeps as a lone
// surrogate is rewritten as its \uXXXX escape. The text then takes Python's
// NaN, Infinity and -Infinity literals.
//
// decoded=false is the ValueError the callers treat as an unavailable runner (a
// JSONDecodeError, a UnicodeDecodeError, or an integer over Python's 4300 digit
// limit). refused=true is a reply Go leaves to Django: JSON nested past the
// decoder's limit (the depth at which Python's json codec raises RecursionError
// is the interpreter's live call stack, not a fixed number) and a lone
// surrogate, which DRF's renderer refuses only where it reaches the response.
func RunnerJSONDecode(raw []byte) (value any, decoded, refused bool) {
	text, ok := runnerJSONText(raw)
	if !ok {
		return nil, false, false
	}
	if auditJSONDepth(text) > runnerJSONDepth || auditLoneSurrogate(text) {
		return nil, false, true
	}
	data := []byte(text)
	value, err := parseProfileTablesJSONConstants(data, pythonJSONConstants(data), true)
	switch {
	case err == nil:
		return value, true, false
	case errors.Is(err, ErrReferenceNesting):
		return nil, false, true
	default:
		return nil, false, false
	}
}

// runnerJSONText is bytes.decode(detect_encoding(raw), 'surrogatepass'): the
// codec json.loads selects, ok=false for a UnicodeDecodeError.
func runnerJSONText(raw []byte) (string, bool) {
	switch {
	case bytes.HasPrefix(raw, []byte{0x00, 0x00, 0xfe, 0xff}):
		return runnerJSONUTF32(raw[4:], binary.BigEndian)
	case bytes.HasPrefix(raw, []byte{0xff, 0xfe, 0x00, 0x00}):
		return runnerJSONUTF32(raw[4:], binary.LittleEndian)
	case bytes.HasPrefix(raw, []byte{0xfe, 0xff}):
		return runnerJSONUTF16(raw[2:], binary.BigEndian)
	case bytes.HasPrefix(raw, []byte{0xff, 0xfe}):
		return runnerJSONUTF16(raw[2:], binary.LittleEndian)
	case bytes.HasPrefix(raw, []byte{0xef, 0xbb, 0xbf}):
		return runnerJSONUTF8(raw[3:])
	case len(raw) >= 4 && raw[0] == 0:
		// 00 00 00 xx is utf-32-be, 00 xx 00 xx is utf-16-be.
		if raw[1] == 0 {
			return runnerJSONUTF32(raw, binary.BigEndian)
		}
		return runnerJSONUTF16(raw, binary.BigEndian)
	case len(raw) >= 4 && raw[1] == 0:
		// xx 00 00 00 is utf-32-le, xx 00 00 xx and xx 00 xx -- are utf-16-le.
		if raw[2] == 0 && raw[3] == 0 {
			return runnerJSONUTF32(raw, binary.LittleEndian)
		}
		return runnerJSONUTF16(raw, binary.LittleEndian)
	case len(raw) == 2 && raw[0] == 0:
		return runnerJSONUTF16(raw, binary.BigEndian)
	case len(raw) == 2 && raw[1] == 0:
		return runnerJSONUTF16(raw, binary.LittleEndian)
	}
	return runnerJSONUTF8(raw)
}

// runnerJSONUTF8 is the utf-8 codec with surrogatepass: a code unit written as
// its three bytes is kept, every other ill-formed sequence is rejected.
func runnerJSONUTF8(raw []byte) (string, bool) {
	var out strings.Builder
	out.Grow(len(raw))
	for i := 0; i < len(raw); {
		if raw[i] < utf8.RuneSelf {
			out.WriteByte(raw[i])
			i++
			continue
		}
		if runnerJSONSurrogateByte(raw, i) {
			unit := rune(raw[i]&0x0f)<<12 | rune(raw[i+1]&0x3f)<<6 | rune(raw[i+2]&0x3f)
			out.WriteString(runnerJSONSurrogateEscape(unit))
			i += 3
			continue
		}
		value, size := utf8.DecodeRune(raw[i:])
		if value == utf8.RuneError && size <= 1 {
			return "", false
		}
		out.Write(raw[i : i+size])
		i += size
	}
	return out.String(), true
}

// runnerJSONSurrogateByte reports a surrogate code unit's three bytes at i.
func runnerJSONSurrogateByte(raw []byte, i int) bool {
	return i+2 < len(raw) && raw[i] == 0xed && raw[i+1] >= 0xa0 && raw[i+1] <= 0xbf && raw[i+2] >= 0x80 && raw[i+2] <= 0xbf
}

// runnerJSONUTF16 is the utf-16 codec with surrogatepass.
func runnerJSONUTF16(raw []byte, order binary.ByteOrder) (string, bool) {
	if len(raw)%2 != 0 {
		return "", false
	}
	var out strings.Builder
	out.Grow(len(raw))
	for i := 0; i < len(raw); i += 2 {
		unit := rune(order.Uint16(raw[i:]))
		switch {
		case unit >= 0xd800 && unit <= 0xdbff && i+4 <= len(raw):
			low := rune(order.Uint16(raw[i+2:]))
			if low < 0xdc00 || low > 0xdfff {
				out.WriteString(runnerJSONSurrogateEscape(unit))
				continue
			}
			out.WriteRune(utf16.DecodeRune(unit, low))
			i += 2
		case unit >= 0xd800 && unit <= 0xdfff:
			out.WriteString(runnerJSONSurrogateEscape(unit))
		default:
			out.WriteRune(unit)
		}
	}
	return out.String(), true
}

// runnerJSONUTF32 is the utf-32 codec with surrogatepass: a value above the
// last code point is a UnicodeDecodeError.
func runnerJSONUTF32(raw []byte, order binary.ByteOrder) (string, bool) {
	if len(raw)%4 != 0 {
		return "", false
	}
	var out strings.Builder
	out.Grow(len(raw))
	for i := 0; i < len(raw); i += 4 {
		value := order.Uint32(raw[i:])
		if value > 0x10ffff {
			return "", false
		}
		if unit := rune(value); unit >= 0xd800 && unit <= 0xdfff {
			out.WriteString(runnerJSONSurrogateEscape(unit))
			continue
		}
		out.WriteRune(rune(value))
	}
	return out.String(), true
}

// runnerJSONSurrogateEscape is one lone code unit as the escape the JSON
// scanner reads back as the same surrogate.
func runnerJSONSurrogateEscape(unit rune) string {
	const digits = "0123456789abcdef"
	return `\u` + string([]byte{digits[(unit>>12)&0xf], digits[(unit>>8)&0xf], digits[(unit>>4)&0xf], digits[unit&0xf]})
}

// pythonJSONConstants is json.loads' parse_constant for Python's NaN, Infinity
// and -Infinity literals: each token is blanked out of data (a "0" the scanner
// reads as a number) and the float it stands for is keyed by the token's end
// offset.
func pythonJSONConstants(data []byte) map[int64]any {
	constants := map[int64]any{}
	for i := 0; i < len(data); {
		switch c := data[i]; {
		case c == '"':
			i++
			for i < len(data) && data[i] != '"' {
				if data[i] == '\\' {
					i++
				}
				i++
			}
			i++
		case c == 'N' && bytes.HasPrefix(data[i:], []byte("NaN")):
			copy(data[i:i+3], "  0")
			constants[int64(i+3)] = math.NaN()
			i += 3
		case c == 'I' && bytes.HasPrefix(data[i:], []byte("Infinity")):
			sign, start := 1, i
			if i > 0 && data[i-1] == '-' {
				sign, start = -1, i-1
			}
			copy(data[start:i+8], strings.Repeat(" ", i+8-start-1)+"0")
			constants[int64(i+8)] = math.Inf(sign)
			i += 8
		default:
			i++
		}
	}
	return constants
}
