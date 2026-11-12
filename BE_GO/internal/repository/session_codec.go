package repository

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"math"
	"math/big"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

const maxDjangoSessionBytes = 1 << 20

type pickleSessionMark struct{}
type pickleSessionList struct{ items []any }

// A closed protocol-4/5 data decoder, never a Python object interpreter. Only
// primitive values and containers are accepted; GLOBAL/REDUCE/BUILD, persistent
// references and object constructors cannot execute. Limits are Go budgets.
func decodeDjangoSession(ctx context.Context, data []byte) (any, error) {
	bad := domain.ErrInvalidCachedSession
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if len(data) == 0 || len(data) > maxDjangoSessionBytes {
		return nil, bad
	}
	// django-redis stores integers directly rather than pickling them.
	if data[0] != 0x80 {
		text := string(data)
		if len(text) > 4300 || strings.Trim(strings.TrimPrefix(text, "-"), "0123456789") != "" {
			return nil, bad
		}
		value, ok := new(big.Int).SetString(text, 10)
		if !ok {
			return nil, bad
		}
		return json.Number(value.String()), nil
	}
	if len(data) < 3 || (data[1] != 4 && data[1] != 5) {
		return nil, bad
	}
	pos, frameEnd := 2, 0
	stack := []any{}
	memo := map[int]any{}
	take := func(n int) ([]byte, bool) {
		if n < 0 || n > len(data)-pos || (frameEnd > pos && n > frameEnd-pos) {
			return nil, false
		}
		value := data[pos : pos+n]
		pos += n
		return value, true
	}
	pop := func() (any, bool) {
		if len(stack) == 0 {
			return nil, false
		}
		value := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		return value, true
	}
	marked := func() ([]any, bool) {
		for i := len(stack) - 1; i >= 0; i-- {
			if _, ok := stack[i].(pickleSessionMark); ok {
				items := append([]any(nil), stack[i+1:]...)
				stack = stack[:i]
				return items, true
			}
		}
		return nil, false
	}
	setPairs := func(items []any) bool {
		if len(stack) == 0 || len(items)%2 != 0 {
			return false
		}
		object, ok := stack[len(stack)-1].(map[string]any)
		if !ok {
			return false
		}
		for i := 0; i < len(items); i += 2 {
			key, ok := items[i].(string)
			if !ok {
				return false
			}
			object[key] = items[i+1]
		}
		return len(object) <= 8192
	}
	for operations := 0; pos < len(data); operations++ {
		if operations%256 == 0 {
			if err := ctx.Err(); err != nil {
				return nil, err
			}
		}
		if len(stack) > 8192 || len(memo) > 8192 {
			return nil, bad
		}
		op := data[pos]
		pos++
		switch op {
		case 0x95: // FRAME
			if frameEnd != 0 && pos-1 != frameEnd {
				return nil, bad
			}
			frameEnd = 0
			raw, ok := take(8)
			if !ok {
				return nil, bad
			}
			size := binary.LittleEndian.Uint64(raw)
			if size > uint64(len(data)-pos) {
				return nil, bad
			}
			frameEnd = pos + int(size)
		case '}':
			stack = append(stack, map[string]any{})
		case ']':
			stack = append(stack, &pickleSessionList{})
		case ')':
			stack = append(stack, []any{})
		case '(':
			stack = append(stack, pickleSessionMark{})
		case 'N':
			stack = append(stack, nil)
		case 0x88:
			stack = append(stack, true)
		case 0x89:
			stack = append(stack, false)
		case 0x8c, 'X', 0x8d, 'C', 'B', 0x8e:
			width := 1
			if op == 'X' || op == 'B' {
				width = 4
			}
			if op == 0x8d || op == 0x8e {
				width = 8
			}
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
			case 8:
				size = binary.LittleEndian.Uint64(raw)
			}
			if size > uint64(len(data)-pos) {
				return nil, bad
			}
			raw, ok = take(int(size))
			if !ok {
				return nil, bad
			}
			if op == 0x8c || op == 'X' || op == 0x8d {
				if !utf8.Valid(raw) {
					return nil, bad
				}
				stack = append(stack, string(raw))
			} else {
				stack = append(stack, raw)
			}
		case 'K', 'M', 'J':
			width := 1
			if op == 'M' {
				width = 2
			}
			if op == 'J' {
				width = 4
			}
			raw, ok := take(width)
			if !ok {
				return nil, bad
			}
			var value int64
			switch width {
			case 1:
				value = int64(raw[0])
			case 2:
				value = int64(binary.LittleEndian.Uint16(raw))
			case 4:
				value = int64(int32(binary.LittleEndian.Uint32(raw)))
			}
			stack = append(stack, json.Number(strconv.FormatInt(value, 10)))
		case 0x8a, 0x8b: // signed little-endian LONG1/LONG4
			width := 1
			if op == 0x8b {
				width = 4
			}
			raw, ok := take(width)
			if !ok {
				return nil, bad
			}
			size := uint64(raw[0])
			if width == 4 {
				size = uint64(binary.LittleEndian.Uint32(raw))
			}
			if size > 2048 {
				return nil, bad
			}
			raw, ok = take(int(size))
			if !ok {
				return nil, bad
			}
			reversed := make([]byte, len(raw))
			for i, b := range raw {
				reversed[len(raw)-1-i] = b
			}
			value := new(big.Int).SetBytes(reversed)
			if len(raw) > 0 && raw[len(raw)-1]&0x80 != 0 {
				value.Sub(value, new(big.Int).Lsh(big.NewInt(1), uint(8*len(raw))))
			}
			stack = append(stack, json.Number(value.String()))
		case 'G':
			raw, ok := take(8)
			if !ok {
				return nil, bad
			}
			stack = append(stack, math.Float64frombits(binary.BigEndian.Uint64(raw)))
		case 0x94, 'q', 'r':
			if len(stack) == 0 {
				return nil, bad
			}
			index := len(memo)
			if op != '\x94' {
				width := 1
				if op == 'r' {
					width = 4
				}
				raw, ok := take(width)
				if !ok {
					return nil, bad
				}
				index = int(raw[0])
				if width == 4 {
					index = int(binary.LittleEndian.Uint32(raw))
				}
			}
			if index > 8191 {
				return nil, bad
			}
			memo[index] = stack[len(stack)-1]
		case 'h', 'j':
			width := 1
			if op == 'j' {
				width = 4
			}
			raw, ok := take(width)
			if !ok {
				return nil, bad
			}
			index := int(raw[0])
			if width == 4 {
				index = int(binary.LittleEndian.Uint32(raw))
			}
			value, exists := memo[index]
			if !exists {
				return nil, bad
			}
			stack = append(stack, value)
		case 'u':
			items, ok := marked()
			if !ok || !setPairs(items) {
				return nil, bad
			}
		case 's':
			value, ok := pop()
			if !ok {
				return nil, bad
			}
			key, ok := pop()
			if !ok || !setPairs([]any{key, value}) {
				return nil, bad
			}
		case 'e', 'a':
			var items []any
			var ok bool
			if op == 'e' {
				items, ok = marked()
			} else {
				var value any
				value, ok = pop()
				items = []any{value}
			}
			if !ok || len(stack) == 0 {
				return nil, bad
			}
			list, ok := stack[len(stack)-1].(*pickleSessionList)
			if !ok {
				return nil, bad
			}
			if len(list.items)+len(items) > 8192 {
				return nil, bad
			}
			list.items = append(list.items, items...)
		case 't', 0x85, 0x86, 0x87:
			var items []any
			if op == 't' {
				var ok bool
				items, ok = marked()
				if !ok {
					return nil, bad
				}
			} else {
				count := int(op - 0x84)
				if count > len(stack) {
					return nil, bad
				}
				items = append([]any(nil), stack[len(stack)-count:]...)
				stack = stack[:len(stack)-count]
			}
			stack = append(stack, items)
		case '.':
			if len(stack) != 1 {
				return nil, bad
			}
			budget := 8191 // Include the root in an 8192-value output budget.
			return sessionContainers(ctx, stack[0], 0, map[*pickleSessionList]bool{}, &budget)
		default:
			return nil, bad
		}
	}
	return nil, bad
}

// Convert mutable pickle lists after all memo references have been resolved.
// Recursive containers cannot occur in JSON-based application session state.
func sessionContainers(ctx context.Context, value any, depth int, lists map[*pickleSessionList]bool, budget *int) (any, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if depth > 128 {
		return nil, domain.ErrInvalidCachedSession
	}
	// Reserve child slots before allocation. Shared memo containers can form a
	// small DAG whose materialized tree grows exponentially without this bound.
	reserve := func(count int) bool {
		if count > *budget {
			return false
		}
		*budget -= count
		return true
	}
	switch current := value.(type) {
	case *pickleSessionList:
		if lists[current] || !reserve(len(current.items)) {
			return nil, domain.ErrInvalidCachedSession
		}
		lists[current] = true
		defer delete(lists, current)
		result := make([]any, len(current.items))
		for i, item := range current.items {
			converted, err := sessionContainers(ctx, item, depth+1, lists, budget)
			if err != nil {
				return nil, err
			}
			result[i] = converted
		}
		return result, nil
	case []any:
		if !reserve(len(current)) {
			return nil, domain.ErrInvalidCachedSession
		}
		result := make([]any, len(current))
		for i, item := range current {
			converted, err := sessionContainers(ctx, item, depth+1, lists, budget)
			if err != nil {
				return nil, err
			}
			result[i] = converted
		}
		return result, nil
	case map[string]any:
		if !reserve(len(current)) {
			return nil, domain.ErrInvalidCachedSession
		}
		result := make(map[string]any, len(current))
		for key, item := range current {
			converted, err := sessionContainers(ctx, item, depth+1, lists, budget)
			if err != nil {
				return nil, err
			}
			result[key] = converted
		}
		return result, nil
	case pickleSessionMark:
		return nil, domain.ErrInvalidCachedSession
	default:
		return value, nil
	}
}
