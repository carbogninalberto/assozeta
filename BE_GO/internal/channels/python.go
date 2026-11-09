package channels

import (
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strconv"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// Dict is a Python dict with string keys, in insertion order.
type Dict []Item

// Item is one Dict entry.
type Item struct {
	Key   string
	Value any
}

// Message values: nil (None), bool, int, int64, uint64, string (valid
// UTF-8), []any (list) and Dict. Python cannot hold invalid UTF-8 text or
// duplicate dict keys, so both are refused, as are floats, bytes and every
// other type, whose encodings are not reproduced here.

// ErrInvalidGroupName is BaseChannelLayer.valid_group_name's refusal.
var ErrInvalidGroupName = errors.New("channels: group name not valid")

// ErrUnsupportedValue is a message value GroupSend cannot encode the way
// msgpack and json.dumps would.
var ErrUnsupportedValue = errors.New("channels: unsupported message value")

// maxNameLength is BaseChannelLayer.MAX_NAME_LENGTH (names must be shorter).
const maxNameLength = 100

// validGroupName is valid_group_name: fewer than 100 characters matching
// ^[a-zA-Z\d\-_.]+$ under Python's re, where \d is any Unicode decimal digit
// and $ also matches before one trailing newline.
func validGroupName(name string) error {
	if !utf8.ValidString(name) || utf8.RuneCountInString(name) >= maxNameLength {
		return fmt.Errorf("%w: %q", ErrInvalidGroupName, name)
	}
	body := name
	if len(body) > 0 && body[len(body)-1] == '\n' {
		body = body[:len(body)-1]
	}
	if body == "" {
		return fmt.Errorf("%w: %q", ErrInvalidGroupName, name)
	}
	for _, r := range body {
		ascii := r < utf8.RuneSelf && (r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r == '-' || r == '_' || r == '.')
		if !ascii && !unicode.IsDigit(r) {
			return fmt.Errorf("%w: %q", ErrInvalidGroupName, name)
		}
	}
	return nil
}

func validateValue(value any) error {
	switch v := value.(type) {
	case nil, bool, int, int64, uint64:
		return nil
	case string:
		if !utf8.ValidString(v) {
			return fmt.Errorf("%w: invalid UTF-8 text", ErrUnsupportedValue)
		}
		return nil
	case []any:
		for _, element := range v {
			if err := validateValue(element); err != nil {
				return err
			}
		}
		return nil
	case Dict:
		seen := make(map[string]struct{}, len(v))
		for _, item := range v {
			if _, duplicate := seen[item.Key]; duplicate {
				return fmt.Errorf("%w: duplicate key %q", ErrUnsupportedValue, item.Key)
			}
			seen[item.Key] = struct{}{}
			if err := validateValue(item.Key); err != nil {
				return err
			}
			if err := validateValue(item.Value); err != nil {
				return err
			}
		}
		return nil
	default:
		return fmt.Errorf("%w: %T", ErrUnsupportedValue, value)
	}
}

// appendMsgpack is msgpack.packb(value) as channels_redis's serializer calls
// it (msgpack 1.x defaults: use_bin_type=True, so text is str8/16/32) for a
// value validateValue accepted. Integers take the smallest format of the C
// packer: positive fixint, then uint8..uint64 for non-negative values,
// negative fixint, then int8..int64.
func appendMsgpack(b []byte, value any) []byte {
	switch v := value.(type) {
	case nil:
		return append(b, 0xc0)
	case bool:
		if v {
			return append(b, 0xc3)
		}
		return append(b, 0xc2)
	case int:
		return appendMsgpackInt(b, int64(v))
	case int64:
		return appendMsgpackInt(b, v)
	case uint64:
		if v > math.MaxInt64 {
			return binary.BigEndian.AppendUint64(append(b, 0xcf), v)
		}
		return appendMsgpackInt(b, int64(v))
	case string:
		b = appendMsgpackHeader(b, len(v), 0xa0, 32, 0xd9, 0xda, 0xdb)
		return append(b, v...)
	case []any:
		b = appendMsgpackHeader(b, len(v), 0x90, 16, 0, 0xdc, 0xdd)
		for _, element := range v {
			b = appendMsgpack(b, element)
		}
		return b
	case Dict:
		b = appendMsgpackHeader(b, len(v), 0x80, 16, 0, 0xde, 0xdf)
		for _, item := range v {
			b = appendMsgpack(b, item.Key)
			b = appendMsgpack(b, item.Value)
		}
		return b
	}
	panic(fmt.Sprintf("channels: unvalidated message value %T", value))
}

// appendMsgpackHeader writes a str/array/map header: the fix form below
// fixLimit, then the 8-bit (when the type has one), 16-bit and 32-bit forms.
func appendMsgpackHeader(b []byte, n int, fix byte, fixLimit int, code8, code16, code32 byte) []byte {
	switch {
	case n < fixLimit:
		return append(b, fix|byte(n))
	case code8 != 0 && n <= math.MaxUint8:
		return append(b, code8, byte(n))
	case n <= math.MaxUint16:
		return binary.BigEndian.AppendUint16(append(b, code16), uint16(n))
	default:
		return binary.BigEndian.AppendUint32(append(b, code32), uint32(n))
	}
}

func appendMsgpackInt(b []byte, v int64) []byte {
	switch {
	case v >= 0 && v < 1<<7, v < 0 && v >= -(1<<5):
		return append(b, byte(v))
	case v >= 0 && v < 1<<8:
		return append(b, 0xcc, byte(v))
	case v >= 0 && v < 1<<16:
		return binary.BigEndian.AppendUint16(append(b, 0xcd), uint16(v))
	case v >= 0 && v < 1<<32:
		return binary.BigEndian.AppendUint32(append(b, 0xce), uint32(v))
	case v >= 0:
		return binary.BigEndian.AppendUint64(append(b, 0xcf), uint64(v))
	case v >= -(1 << 7):
		return append(b, 0xd0, byte(v))
	case v >= -(1 << 15):
		return binary.BigEndian.AppendUint16(append(b, 0xd1), uint16(v))
	case v >= -(1 << 31):
		return binary.BigEndian.AppendUint32(append(b, 0xd2), uint32(v))
	default:
		return binary.BigEndian.AppendUint64(append(b, 0xd3), uint64(v))
	}
}

// envelope is realtime_layer.envelope: json.dumps with the defaults
// (ensure_ascii, ", " and ": ") of the version-1 mirror object.
func envelope(group string, message Dict) []byte {
	return dto.PythonJSONDumps(domain.RequestObject{
		{Name: "version", Value: json.Number("1")},
		{Name: "group", Value: group},
		{Name: "message", Value: jsonValue(message)},
	})
}

// jsonValue maps a validated message value onto dto.PythonJSONDumps' types.
func jsonValue(value any) any {
	switch v := value.(type) {
	case int:
		return json.Number(strconv.Itoa(v))
	case int64:
		return json.Number(strconv.FormatInt(v, 10))
	case uint64:
		return json.Number(strconv.FormatUint(v, 10))
	case []any:
		list := make([]any, len(v))
		for i, element := range v {
			list[i] = jsonValue(element)
		}
		return list
	case Dict:
		object := make(domain.RequestObject, len(v))
		for i, item := range v {
			object[i] = domain.RequestField{Name: item.Key, Value: jsonValue(item.Value)}
		}
		return object
	}
	return value
}

// pythonTime is time.time() for t, converted as CPython's
// _PyTime_AsSecondsDouble does: whole seconds exactly, otherwise
// (double)ns / 1e9. The double rounding can carry a time just before a
// second boundary onto it (int(time.time()) is then the next second).
func pythonTime(t time.Time) float64 {
	ns := t.UnixNano()
	if ns%int64(time.Second) == 0 {
		return float64(ns / int64(time.Second))
	}
	return float64(ns) / 1e9
}

// pythonTimeRepr is repr(time.time()), the form redis-py's encoder sends a
// float in. Python's json.dumps writes finite floats with the same repr.
func pythonTimeRepr(t time.Time) string {
	return string(dto.PythonJSONDumps(pythonTime(t)))
}
