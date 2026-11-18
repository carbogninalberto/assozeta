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

// referenceJSONNesting is the default parser limit for request bodies and
// stored values (deep form JSON uses its own, measured limit).
const referenceJSONNesting = 1000

// ParseReferenceJSON keeps integer precision and dictionary insertion order,
// including Python JSON's last-value/first-position behavior for duplicate keys.
// The HTTP transport must independently bound request bytes and upload sizes.
func ParseReferenceJSON(data []byte) (any, error) {
	return parseReferenceJSON(data, nil, false)
}

func parseReferenceJSON(data []byte, constants map[int64]any, rateLimit bool) (any, error) {
	return parseReferenceJSONLimit(data, constants, rateLimit, referenceJSONNesting)
}

// AuditJSONDepth is auditJSONDepth: the container nesting of a whole JSON text
// ('[]' is 1, '{"a": {"a": 1}}' is 2).
func AuditJSONDepth(text string) int { return auditJSONDepth(text) }

// JSONValueDepth is the container nesting of a parsed JSON value (the top-level
// container is 1; a scalar is 0). It is what json.dumps walks, so for a value
// parsed from text without duplicate keys it equals AuditJSONDepth of that
// text, while a duplicate key's overwritten deep value no longer counts.
// Iterative: the band depths are past a recursive walk's own stack.
func JSONValueDepth(value any) int {
	type frame struct {
		value any
		depth int
	}
	deepest := 0
	stack := []frame{{value: value}}
	for len(stack) > 0 {
		current := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		var children []any
		switch value := current.value.(type) {
		case domain.RequestObject:
			children = make([]any, 0, len(value))
			for _, field := range value {
				children = append(children, field.Value)
			}
		case []any:
			children = value
		default:
			continue
		}
		depth := current.depth + 1
		if depth > deepest {
			deepest = depth
		}
		for _, child := range children {
			stack = append(stack, frame{value: child, depth: depth})
		}
	}
	return deepest
}

// ParseStoredJSONDepth is json.loads for a stored jsonb text with the site's
// measured total nesting band: text whose whole depth (AuditJSONDepth) exceeds
// maxDepth is refused with ErrReferenceNesting before parsing, while a document
// at the edge still parses. Python's integer digit limit applies too
// (ErrLoginRateJSONValue). Callers treat every error as json.loads raising.
func ParseStoredJSONDepth(text string, maxDepth int) (any, error) {
	if auditJSONDepth(text) > maxDepth {
		return nil, ErrReferenceNesting
	}
	// The parser reads the innermost scalar one level past the last container, so
	// its depth limit is the document's total depth.
	return parseReferenceJSONLimit([]byte(text), nil, true, maxDepth)
}

// parseReferenceJSONLimit is parseReferenceJSON with an explicit container
// nesting limit (a document nested deeper returns ErrReferenceNesting).
func parseReferenceJSONLimit(data []byte, constants map[int64]any, rateLimit bool, limit int) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var read func(int) (any, error)
	read = func(depth int) (any, error) {
		if depth > limit {
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
