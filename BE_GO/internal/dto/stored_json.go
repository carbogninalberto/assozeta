package dto

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// The bounds of CPython 3.13's json.loads reading a jsonb column, measured
// with json.loads('['*n + ']'*n): n=9998 parses at the top of a thread and
// n=9999 raises RecursionError. The C scanner enters one recursive call per
// container and the depth it reaches falls with the C calls already on the
// caller's stack (a Django request thread keeps well under 200), so only the
// band below the floor depends on the caller.
const (
	// StoredJSONRecursionFloor is a depth every call stack raises on.
	StoredJSONRecursionFloor = 9999
	// StoredJSONRecursionBand is the depth from which the outcome depends on
	// the C calls already on the request thread's stack.
	StoredJSONRecursionBand = 9800
	// StoredJSONIntegerDigits is sys.int_max_str_digits: json.loads raises
	// ValueError on an integer literal with more digits.
	StoredJSONIntegerDigits = 4300
)

// ErrStoredJSONRecursion is json.loads' RecursionError on a nesting depth that
// raises on every call stack (the view does not catch it: the 500 page).
var ErrStoredJSONRecursion = errors.New("stored JSON nests past json.loads")

// ErrJSONRecursionBand is json.loads nesting in the band whose RecursionError
// depth depends on the C calls already on the request thread's stack: the
// same document parses or raises depending on the caller (long-tail rule 4,
// platform), so the request is handed to Django. Every json.loads port that
// reads nesting this deep returns it.
var ErrJSONRecursionBand = refusal.New("json.loads nesting in CPython's C recursion band")

// ErrStoredJSONInteger is json.loads' ValueError on an integer literal past
// sys.int_max_str_digits (the view does not catch it: the 500 page).
var ErrStoredJSONInteger = errors.New("stored JSON integer past CPython's digit limit")

// ErrStoredJSONUnreachable is column text jsonb never stores (a lone surrogate
// escape, the NaN and Infinity literals): json.loads would read it, but no
// Django write path can put it in a JSONField, so it is app state.
var ErrStoredJSONUnreachable = refusal.AppState("stored JSON text jsonb does not store")

// StoredJSON parses a jsonb column the way the view's Django process reads it:
// psycopg decodes the column text with json.loads, so nil is SQL NULL and every
// other value is the parsed one (nil, bool, string, json.Number for both int
// and float literals, RequestObject for objects, []any for arrays).
//
// Depths at or past StoredJSONRecursionFloor answer ErrStoredJSONRecursion and
// depths in [StoredJSONRecursionBand, floor) answer ErrJSONRecursionBand,
// as json.loads' C recursion is bounded by the live stack. Text jsonb never
// stores (a lone surrogate escape, NaN or Infinity) is ErrStoredJSONUnreachable.
func StoredJSON(text *string) (any, error) {
	if text == nil {
		return nil, nil
	}
	depth := auditJSONDepth(*text)
	switch {
	case depth >= StoredJSONRecursionFloor:
		return nil, ErrStoredJSONRecursion
	case depth >= StoredJSONRecursionBand:
		return nil, ErrJSONRecursionBand
	case auditLoneSurrogate(*text):
		// jsonb accepts no lone surrogate: PostgreSQL's own parser rejects it.
		return nil, ErrStoredJSONUnreachable
	}
	value, err := parseStoredJSON([]byte(*text))
	switch {
	case errors.Is(err, ErrStoredJSONInteger):
		return nil, err
	case err != nil:
		// jsonb only stores the JSON json.loads reads (NaN and Infinity
		// literals included, which PostgreSQL refuses to store).
		return nil, ErrStoredJSONUnreachable
	}
	return value, nil
}

// parseStoredJSON is json.loads for the text of a jsonb column: container
// nesting is bounded by StoredJSONRecursionBand before the call, so the
// recursion here stays under Go's own JSON nesting limit.
func parseStoredJSON(data []byte) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	var read func() (any, error)
	read = func() (any, error) {
		token, err := decoder.Token()
		if err != nil {
			return nil, err
		}
		delim, container := token.(json.Delim)
		if !container {
			if number, ok := token.(json.Number); ok {
				if err := storedJSONNumber(number); err != nil {
					return nil, err
				}
			}
			return token, nil
		}
		switch delim {
		case '{':
			// Python keeps a duplicate key's last value at its first position.
			object := domain.RequestObject{}
			positions := map[string]int{}
			for decoder.More() {
				key, err := decoder.Token()
				if err != nil {
					return nil, err
				}
				name, ok := key.(string)
				if !ok {
					return nil, domain.ErrInvalidScopedRequest
				}
				value, err := read()
				if err != nil {
					return nil, err
				}
				if at, exists := positions[name]; exists {
					object[at].Value = value
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
				value, err := read()
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
		}
		return nil, domain.ErrInvalidScopedRequest
	}
	value, err := read()
	if err != nil {
		return nil, err
	}
	if _, err := decoder.Token(); err != io.EOF {
		return nil, domain.ErrInvalidScopedRequest
	}
	return value, nil
}

// storedJSONNumber applies CPython's integer conversion limit: an integer
// literal (no fraction or exponent) past 4300 digits makes json.loads raise
// ValueError; a float literal is read through strtod, which has no such limit.
func storedJSONNumber(number json.Number) error {
	text := string(number)
	if strings.ContainsAny(text, ".eE") {
		return nil
	}
	if len(strings.TrimPrefix(text, "-")) > StoredJSONIntegerDigits {
		return ErrStoredJSONInteger
	}
	return nil
}
