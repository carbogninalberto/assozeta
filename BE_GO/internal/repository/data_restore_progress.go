package repository

import (
	"context"
	"encoding/binary"
	"errors"
	"math"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/redis/go-redis/v9"
)

// DataRestoreProgress reads the live progress snapshot the restore worker
// stores with cache.set('data-restore-progress:<id>', payload, 86400) in
// Django's cache (django-redis, Redis DB 1, KEY_PREFIX ”, version 1).
type DataRestoreProgress struct{ client *redis.Client }

func NewDataRestoreProgress(client *redis.Client) (*DataRestoreProgress, error) {
	if client == nil || client.Options().DB != 1 {
		return nil, errors.New("data restore progress requires Django cache database 1")
	}
	return &DataRestoreProgress{client: client}, nil
}

// ErrDataRestoreProgressShape is a stored value outside the decoded subset:
// Django would unpickle it (or fail and answer None); Go refuses the request.
var ErrDataRestoreProgressShape = refusal.New("cached data restore progress not ported")

// DataRestoreProgressValue is cache.get(progress_key(id)) decoded: nil for a
// miss or a cache error (progress.snapshot catches every exception), an int64
// for django-redis' plain integer values, otherwise the unpickled value
// (*PickledDict, []any, string, int64, float64, bool, nil).
type DataRestoreProgressValue struct {
	Found bool
	Value any
}

// Snapshot reads one receipt's progress value.
func (p *DataRestoreProgress) Snapshot(ctx context.Context, id string) (DataRestoreProgressValue, error) {
	raw, err := p.client.Get(ctx, ":1:data-restore-progress:"+id).Bytes()
	if err != nil {
		// A miss is None; any cache error is caught by snapshot's except.
		return DataRestoreProgressValue{}, nil
	}
	// django-redis DefaultClient.decode: int(value) first, then pickle.loads.
	if value, ok := pythonIntBytes(raw); ok {
		return DataRestoreProgressValue{Found: true, Value: value}, nil
	}
	value, err := unpickleDataRestoreProgress(raw)
	if err != nil {
		return DataRestoreProgressValue{}, err
	}
	return DataRestoreProgressValue{Found: true, Value: value}, nil
}

// pythonIntBytes is int(bytes) for the ASCII forms: surrounding whitespace,
// one sign, digits with single underscores between them. A value int() would
// still accept but that does not fit int64 is reported as an int as well
// (only its truthiness and type are used).
func pythonIntBytes(raw []byte) (int64, bool) {
	text := strings.Trim(string(raw), " \t\n\r\v\f")
	if text != "" && (text[0] == '+' || text[0] == '-') {
		text = text[1:]
	}
	if text == "" || text[0] == '_' || text[len(text)-1] == '_' || strings.Contains(text, "__") {
		return 0, false
	}
	var value int64
	for _, c := range []byte(text) {
		if c == '_' {
			continue
		}
		if c < '0' || c > '9' {
			return 0, false
		}
		if value < math.MaxInt64/10 {
			value = value*10 + int64(c-'0')
		} else {
			value = math.MaxInt64
		}
	}
	if value == 0 {
		return 0, true
	}
	return value, true
}

// PickledDict is an unpickled dict with str keys in insertion order.
type PickledDict struct {
	Keys   []string
	Values map[string]any
}

func (d *PickledDict) set(key string, value any) {
	if _, ok := d.Values[key]; !ok {
		d.Keys = append(d.Keys, key)
	}
	d.Values[key] = value
}

type pickledList struct{ items []any }
type pickledTuple []any
type pickledMark struct{}

const maxProgressBytes = 1 << 20

// unpickleDataRestoreProgress reads protocol 2-5 pickles of str-keyed dicts,
// lists, tuples, str, int (up to 64 bits), float, bool and None, with frames
// and memo references. It is a data decoder: no global, reduce or object
// construction exists, and anything else is ErrDataRestoreProgressShape.
func unpickleDataRestoreProgress(data []byte) (any, error) {
	bad := ErrDataRestoreProgressShape
	if len(data) < 3 || len(data) > maxProgressBytes || data[0] != 0x80 || data[1] < 2 || data[1] > 5 {
		return nil, bad
	}
	pos := 2
	var stack []any
	memo := map[uint64]any{}
	take := func(n int) ([]byte, bool) {
		if n < 0 || n > len(data)-pos {
			return nil, false
		}
		value := data[pos : pos+n]
		pos += n
		return value, true
	}
	marked := func() ([]any, bool) {
		for i := len(stack) - 1; i >= 0; i-- {
			if _, ok := stack[i].(pickledMark); ok {
				items := append([]any(nil), stack[i+1:]...)
				stack = stack[:i]
				return items, true
			}
		}
		return nil, false
	}
	setItems := func(items []any) bool {
		if len(items)%2 != 0 || len(stack) == 0 {
			return false
		}
		dict, ok := stack[len(stack)-1].(*PickledDict)
		if !ok {
			return false
		}
		for i := 0; i < len(items); i += 2 {
			key, ok := items[i].(string)
			if !ok {
				return false
			}
			dict.set(key, items[i+1])
		}
		return true
	}
	for pos < len(data) {
		op := data[pos]
		pos++
		switch op {
		case 0x95: // FRAME
			raw, ok := take(8)
			if !ok || binary.LittleEndian.Uint64(raw) > uint64(len(data)-pos) {
				return nil, bad
			}
		case '(':
			stack = append(stack, pickledMark{})
		case '}':
			stack = append(stack, &PickledDict{Values: map[string]any{}})
		case 'd':
			items, ok := marked()
			if !ok {
				return nil, bad
			}
			stack = append(stack, &PickledDict{Values: map[string]any{}})
			if !setItems(items) {
				return nil, bad
			}
		case ']':
			stack = append(stack, &pickledList{})
		case 'l':
			items, ok := marked()
			if !ok {
				return nil, bad
			}
			stack = append(stack, &pickledList{items: items})
		case ')':
			stack = append(stack, pickledTuple{})
		case 't':
			items, ok := marked()
			if !ok {
				return nil, bad
			}
			stack = append(stack, pickledTuple(items))
		case 0x85, 0x86, 0x87: // TUPLE1..3
			n := int(op - 0x84)
			if len(stack) < n {
				return nil, bad
			}
			items := append(pickledTuple(nil), stack[len(stack)-n:]...)
			for _, item := range items {
				if _, isMark := item.(pickledMark); isMark {
					return nil, bad
				}
			}
			stack = append(stack[:len(stack)-n], items)
		case 0x94: // MEMOIZE
			if len(stack) == 0 {
				return nil, bad
			}
			memo[uint64(len(memo))] = stack[len(stack)-1]
		case 'q', 'r': // BINPUT, LONG_BINPUT
			width := 1
			if op == 'r' {
				width = 4
			}
			raw, ok := take(width)
			if !ok || len(stack) == 0 {
				return nil, bad
			}
			index := uint64(raw[0])
			if width == 4 {
				index = uint64(binary.LittleEndian.Uint32(raw))
			}
			memo[index] = stack[len(stack)-1]
		case 'h', 'j': // BINGET, LONG_BINGET
			width := 1
			if op == 'j' {
				width = 4
			}
			raw, ok := take(width)
			if !ok {
				return nil, bad
			}
			index := uint64(raw[0])
			if width == 4 {
				index = uint64(binary.LittleEndian.Uint32(raw))
			}
			value, found := memo[index]
			if !found {
				return nil, bad
			}
			stack = append(stack, value)
		case 0x8c, 'X', 0x8d: // SHORT_BINUNICODE, BINUNICODE, BINUNICODE8
			width := map[byte]int{0x8c: 1, 'X': 4, 0x8d: 8}[op]
			raw, ok := take(width)
			if !ok {
				return nil, bad
			}
			var size uint64
			switch width {
			case 1:
				size = uint64(raw[0])
			case 4:
				size = uint64(binary.LittleEndian.Uint32(raw))
			default:
				size = binary.LittleEndian.Uint64(raw)
			}
			if size > uint64(len(data)) {
				return nil, bad
			}
			text, ok := take(int(size))
			// Lone surrogates (surrogatepass) are outside the subset.
			if !ok || !utf8.Valid(text) {
				return nil, bad
			}
			stack = append(stack, string(text))
		case 'K', 'M', 'J':
			width := map[byte]int{'K': 1, 'M': 2, 'J': 4}[op]
			raw, ok := take(width)
			if !ok {
				return nil, bad
			}
			switch op {
			case 'K':
				stack = append(stack, int64(raw[0]))
			case 'M':
				stack = append(stack, int64(binary.LittleEndian.Uint16(raw)))
			default:
				stack = append(stack, int64(int32(binary.LittleEndian.Uint32(raw))))
			}
		case 0x8a: // LONG1
			raw, ok := take(1)
			if !ok {
				return nil, bad
			}
			body, ok := take(int(raw[0]))
			if !ok || len(body) > 8 {
				return nil, bad
			}
			var value int64
			for i := len(body) - 1; i >= 0; i-- {
				value = value<<8 | int64(body[i])
			}
			if len(body) > 0 && len(body) < 8 && body[len(body)-1]&0x80 != 0 {
				value -= 1 << (8 * len(body))
			}
			stack = append(stack, value)
		case 'G': // BINFLOAT
			raw, ok := take(8)
			if !ok {
				return nil, bad
			}
			stack = append(stack, math.Float64frombits(binary.BigEndian.Uint64(raw)))
		case 0x88, 0x89:
			stack = append(stack, op == 0x88)
		case 'N':
			stack = append(stack, nil)
		case 's':
			if len(stack) < 3 {
				return nil, bad
			}
			items := append([]any(nil), stack[len(stack)-2:]...)
			stack = stack[:len(stack)-2]
			if !setItems(items) {
				return nil, bad
			}
		case 'u':
			items, ok := marked()
			if !ok || !setItems(items) {
				return nil, bad
			}
		case 'a', 'e':
			var items []any
			if op == 'e' {
				var ok bool
				if items, ok = marked(); !ok {
					return nil, bad
				}
			} else {
				if len(stack) < 2 {
					return nil, bad
				}
				items = []any{stack[len(stack)-1]}
				stack = stack[:len(stack)-1]
			}
			if len(stack) == 0 {
				return nil, bad
			}
			list, ok := stack[len(stack)-1].(*pickledList)
			if !ok {
				return nil, bad
			}
			list.items = append(list.items, items...)
		case '.':
			if len(stack) != 1 || pos != len(data) {
				return nil, bad
			}
			return plainPickled(stack[0], 0)
		default:
			return nil, bad
		}
	}
	return nil, bad
}

// plainPickled turns the decoder's containers into *PickledDict, []any
// (lists and tuples) and scalars; self-references are outside the subset.
func plainPickled(value any, depth int) (any, error) {
	if depth > 64 {
		return nil, ErrDataRestoreProgressShape
	}
	switch v := value.(type) {
	case *PickledDict:
		out := &PickledDict{Keys: v.Keys, Values: make(map[string]any, len(v.Values))}
		for key, item := range v.Values {
			plain, err := plainPickled(item, depth+1)
			if err != nil {
				return nil, err
			}
			out.Values[key] = plain
		}
		return out, nil
	case *pickledList:
		return plainPickledItems(v.items, depth)
	case pickledTuple:
		return plainPickledItems(v, depth)
	case pickledMark:
		return nil, ErrDataRestoreProgressShape
	}
	return value, nil
}

func plainPickledItems(items []any, depth int) ([]any, error) {
	out := make([]any, 0, len(items))
	for _, item := range items {
		plain, err := plainPickled(item, depth+1)
		if err != nil {
			return nil, err
		}
		out = append(out, plain)
	}
	return out, nil
}
