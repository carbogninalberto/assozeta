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

// StoredJSONIntegerDigits is sys.int_max_str_digits: json.loads raises
// ValueError on an integer literal with more digits.
const StoredJSONIntegerDigits = 4300

// ErrStoredJSONRecursion is json.loads' RecursionError on nesting deeper than
// the call site's measured edge (owner decision X15): the depth CPython 3.13's
// C scanner reaches depends on the C calls already on the caller's stack, so
// each caller passes the edge measured at its own call site.
var ErrStoredJSONRecursion = errors.New("stored JSON nests past json.loads")

// StoredJSONRecursionError is that RecursionError. Object selects Python's
// text for the first container the scanner enters past the edge: a JSON
// object or a JSON array.
type StoredJSONRecursionError struct{ Object bool }

func (e *StoredJSONRecursionError) Error() string {
	kind := "array"
	if e.Object {
		kind = "object"
	}
	return "maximum recursion depth exceeded while decoding a JSON " + kind + " from a unicode string"
}

func (e *StoredJSONRecursionError) Is(target error) bool { return target == ErrStoredJSONRecursion }

// ErrStoredJSONInteger is json.loads' ValueError on an integer literal past
// sys.int_max_str_digits (the view does not catch it: the 500 page).
var ErrStoredJSONInteger = errors.New("stored JSON integer past CPython's digit limit")

// ErrStoredJSONUnreachable is column text jsonb never stores (a lone surrogate
// escape, the NaN and Infinity literals): json.loads would read it, but no
// Django write path can put it in a JSONField, so it is app state.
var ErrStoredJSONUnreachable = refusal.AppState("stored JSON text jsonb does not store")

// StoredJSONEdge parses a jsonb column the way the view's Django process reads
// it: psycopg decodes the column text with json.loads, so nil is SQL NULL and
// every other value is the parsed one (nil, bool, string, json.Number for both
// int and float literals, RequestObject for objects, []any for arrays).
//
// edge is the deepest container nesting (AuditJSONDepth: "[]" counts 1) that
// json.loads parses at the caller's Django call site (owner decision X15).
// Deeper text answers ErrStoredJSONRecursion, a *StoredJSONRecursionError that
// names the first container entered past the edge. Text jsonb never stores (a
// lone surrogate escape, NaN or Infinity) is ErrStoredJSONUnreachable.
func StoredJSONEdge(text *string, edge int) (any, error) {
	if text == nil {
		return nil, nil
	}
	if object, past := storedJSONRecursionKind(*text, edge); past {
		return nil, &StoredJSONRecursionError{Object: object}
	}
	if auditLoneSurrogate(*text) {
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

// storedJSONRecursionKind reports whether text nests deeper than edge and, if
// so, whether the first container entered past the edge (document order) is an
// object. The C scanner enters containers depth first, so that is the container
// whose kind Python's RecursionError names.
func storedJSONRecursionKind(text string, edge int) (object, past bool) {
	depth, quoted, escaped := 0, false, false
	for i := 0; i < len(text); i++ {
		c := text[i]
		switch {
		case escaped:
			escaped = false
		case quoted && c == '\\':
			escaped = true
		case c == '"':
			quoted = !quoted
		case !quoted && (c == '[' || c == '{'):
			depth++
			if depth > edge {
				return c == '{', true
			}
		case !quoted && (c == ']' || c == '}'):
			depth--
		}
	}
	return false, false
}

// parseStoredJSON is json.loads for the text of a jsonb column: container
// nesting is bounded by the caller's measured edge before the call, so the
// recursion here stays under Go's own JSON nesting limit.
func parseStoredJSON(data []byte) (any, error) { return parseStoredJSONConstants(data, nil) }

// PythonJSONLoadsEdge is json.loads of a str for JSON text in UTF-8 whose lone
// surrogates are written as \uXXXX escapes: NaN, Infinity and -Infinity
// become float64, a string holding a lone surrogate keeps it (WTF-8), a
// duplicate key keeps its last value at its first position, and an integer
// literal past 4300 digits is ErrStoredJSONInteger (ValueError). Nesting
// deeper than the caller's measured edge (owner decision X15) is
// ErrStoredJSONRecursion, a *StoredJSONRecursionError. Any other error is
// JSONDecodeError.
func PythonJSONLoadsEdge(data []byte, edge int) (any, error) {
	if object, past := storedJSONRecursionKind(string(data), edge); past {
		return nil, &StoredJSONRecursionError{Object: object}
	}
	data, constants := pythonJSONLiteralTokens(data)
	if err := jsonLoneSurrogateStrings(data, constants); err != nil {
		return nil, err
	}
	return parseStoredJSONConstants(data, constants)
}

// parseStoredJSONConstants parses with the values in constants (keyed by the
// end offset of the token they replace) standing for their tokens.
func parseStoredJSONConstants(data []byte, constants map[int64]any) (any, error) {
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
			if value, ok := constants[decoder.InputOffset()]; ok {
				return value, nil
			}
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
				if original, exists := constants[decoder.InputOffset()]; exists {
					key = original
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
