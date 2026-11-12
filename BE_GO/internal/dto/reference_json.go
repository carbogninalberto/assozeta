package dto

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

var ErrReferenceNesting = errors.New("JSON nesting exceeds parser limit")

// ParseReferenceJSON keeps integer precision and dictionary insertion order,
// including Python JSON's last-value/first-position behavior for duplicate keys.
// The HTTP transport must independently bound request bytes and upload sizes.
func ParseReferenceJSON(data []byte) (any, error) {
	return parseReferenceJSON(data, nil, false)
}

func parseReferenceJSON(data []byte, constants map[int64]any, rateLimit bool) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var read func(int) (any, error)
	read = func(depth int) (any, error) {
		if depth > 1000 {
			return nil, ErrReferenceNesting
		}
		token, err := decoder.Token()
		if err != nil {
			return nil, err
		}
		delim, container := token.(json.Delim)
		if !container {
			if value, ok := constants[decoder.InputOffset()]; ok {
				return value, nil
			}
			if number, ok := token.(json.Number); ok && rateLimit && !strings.ContainsAny(string(number), ".eE") && len(strings.TrimPrefix(string(number), "-")) > 4300 {
				return nil, ErrLoginRateJSONValue
			}
			return token, nil
		}
		switch delim {
		case '{':
			object := domain.RequestObject{}
			positions := map[string]int{}
			for decoder.More() {
				key, err := decoder.Token()
				if err != nil {
					return nil, err
				}
				if original, exists := constants[decoder.InputOffset()]; exists {
					key = original
				}
				name, ok := key.(string)
				if !ok {
					return nil, domain.ErrInvalidScopedRequest
				}
				value, err := read(depth + 1)
				if err != nil {
					return nil, err
				}
				if i, exists := positions[name]; exists {
					object[i].Value = value
				} else {
					positions[name] = len(object)
					object = append(object, domain.RequestField{Name: name, Value: value})
				}
			}
			end, err := decoder.Token()
			if err != nil || end != json.Delim('}') {
				return nil, domain.ErrInvalidScopedRequest
			}
			return object, nil
		case '[':
			array := []any{}
			for decoder.More() {
				value, err := read(depth + 1)
				if err != nil {
					return nil, err
				}
				array = append(array, value)
			}
			end, err := decoder.Token()
			if err != nil || end != json.Delim(']') {
				return nil, domain.ErrInvalidScopedRequest
			}
			return array, nil
		default:
			return nil, domain.ErrInvalidScopedRequest
		}
	}
	value, err := read(0)
	if err != nil {
		return nil, err
	}
	if _, err := decoder.Token(); err != io.EOF {
		return nil, domain.ErrInvalidScopedRequest
	}
	return value, nil
}
