package celery

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strconv"
	"strings"
	"unicode/utf16"

	"github.com/google/uuid"
)

// Number is a JSON number kept as its text: json.loads gives an int (kept
// as written) or a float, which json.dumps writes back as repr(float).
type Number string

// ErrUndecodable is a broker payload or body outside the decoded subset.
var ErrUndecodable = errors.New("celery: undecodable message")

func appendNumber(b []byte, n Number) ([]byte, error) {
	text := string(n)
	if !strings.ContainsAny(text, ".eE") {
		return append(b, text...), nil
	}
	value, err := strconv.ParseFloat(text, 64)
	if err != nil {
		return nil, fmt.Errorf("%w: number %q", ErrUnsupportedValue, text)
	}
	return append(b, pythonFloat(value)...), nil
}

// DecodeJSON is json.loads with insertion-ordered objects (Dict), Number
// for numbers and Go strings; text with an unpaired surrogate escape (which
// Python keeps and Go cannot) is refused.
func DecodeJSON(data []byte) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	value, err := decodeValue(decoder)
	if err != nil {
		return nil, err
	}
	if _, err = decoder.Token(); err != io.EOF {
		return nil, fmt.Errorf("%w: trailing data", ErrUndecodable)
	}
	if err = loneSurrogates(data); err != nil {
		return nil, err
	}
	return value, nil
}

func decodeValue(decoder *json.Decoder) (any, error) {
	token, err := decoder.Token()
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrUndecodable, err)
	}
	switch v := token.(type) {
	case json.Delim:
		switch v {
		case '{':
			out := Dict{}
			for decoder.More() {
				key, err := decoder.Token()
				if err != nil {
					return nil, fmt.Errorf("%w: %v", ErrUndecodable, err)
				}
				name, _ := key.(string)
				value, err := decodeValue(decoder)
				if err != nil {
					return nil, err
				}
				replaced := false
				for i := range out {
					if out[i].Key == name { // Python keeps the last value at the first position
						out[i].Value, replaced = value, true
					}
				}
				if !replaced {
					out = append(out, Item{Key: name, Value: value})
				}
			}
			_, err = decoder.Token()
			return out, err
		case '[':
			out := []any{}
			for decoder.More() {
				value, err := decodeValue(decoder)
				if err != nil {
					return nil, err
				}
				out = append(out, value)
			}
			_, err = decoder.Token()
			return out, err
		}
	case json.Number:
		return Number(v), nil
	case string, bool, nil:
		return v, nil
	}
	return nil, fmt.Errorf("%w: token %v", ErrUndecodable, token)
}

// loneSurrogates refuses \uD800-\uDFFF escapes that do not form a pair.
func loneSurrogates(data []byte) error {
	for i := 0; i+5 < len(data); i++ {
		if data[i] != '\\' {
			continue
		}
		if data[i+1] != 'u' {
			i++
			continue
		}
		code, err := strconv.ParseUint(string(data[i+2:i+6]), 16, 16)
		if err != nil {
			return fmt.Errorf("%w: escape", ErrUndecodable)
		}
		r := rune(code)
		if utf16.IsSurrogate(r) {
			if r >= 0xdc00 || i+11 >= len(data) || data[i+6] != '\\' || data[i+7] != 'u' {
				return fmt.Errorf("%w: unpaired surrogate", ErrUndecodable)
			}
			low, err := strconv.ParseUint(string(data[i+8:i+12]), 16, 16)
			if err != nil || low < 0xdc00 || low > 0xdfff {
				return fmt.Errorf("%w: unpaired surrogate", ErrUndecodable)
			}
			i += 11
			continue
		}
		i += 5
	}
	return nil
}

// EncodeJSON is kombu.utils.json.dumps of a decoded value.
func EncodeJSON(value any) ([]byte, error) { return appendJSON(nil, value) }

// KombuValue applies kombu's JSON object hook to a decoded body: the
// registered "uuid" type becomes a uuid.UUID; other registered types
// (datetime, decimal, bytes, ...) are outside the subset.
func KombuValue(value any) (any, error) {
	switch v := value.(type) {
	case Dict:
		if typ, ok := v.Get("__type__"); ok {
			if inner, ok := v.Get("__value__"); ok && len(v) == 2 {
				if typ == "uuid" {
					fields, _ := inner.(Dict)
					if text, ok := fields.Get("hex"); ok && len(fields) == 1 {
						if s, ok := text.(string); ok {
							raw, err := hex.DecodeString(s)
							if err == nil && len(raw) == 16 {
								return uuid.UUID(raw), nil
							}
						}
					}
				}
				return nil, fmt.Errorf("%w: kombu type %v", ErrUndecodable, typ)
			}
		}
		out := make(Dict, len(v))
		for i, item := range v {
			decoded, err := KombuValue(item.Value)
			if err != nil {
				return nil, err
			}
			out[i] = Item{Key: item.Key, Value: decoded}
		}
		return out, nil
	case []any:
		out := make([]any, len(v))
		for i, item := range v {
			decoded, err := KombuValue(item)
			if err != nil {
				return nil, err
			}
			out[i] = decoded
		}
		return out, nil
	}
	return value, nil
}

// Get returns the value of key.
func (d Dict) Get(key string) (any, bool) {
	for _, item := range d {
		if item.Key == key {
			return item.Value, true
		}
	}
	return nil, false
}

// Set replaces the value of key in place or appends it, as a Python dict
// assignment does.
func (d Dict) Set(key string, value any) Dict {
	for i := range d {
		if d[i].Key == key {
			d[i].Value = value
			return d
		}
	}
	return append(d, Item{Key: key, Value: value})
}

// Clone copies nested Dicts and lists.
func Clone(value any) any {
	switch v := value.(type) {
	case Dict:
		out := make(Dict, len(v))
		for i, item := range v {
			out[i] = Item{Key: item.Key, Value: Clone(item.Value)}
		}
		return out
	case []any:
		out := make([]any, len(v))
		for i, item := range v {
			out[i] = Clone(item)
		}
		return out
	}
	return value
}
