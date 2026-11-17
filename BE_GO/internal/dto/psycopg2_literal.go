package dto

import (
	"encoding/json"
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// Psycopg2AdaptError is an exception psycopg2 raises while adapting a parameter (before anything is sent).
type Psycopg2AdaptError struct{ Text string }

func (e *Psycopg2AdaptError) Error() string { return e.Text }

// Psycopg2Literal is the SQL literal psycopg2 writes for a value Python's json.loads produced. The value is a parsed
// JSON value as this package represents it (see ParsePythonJSON in websocket_json.go): nil, bool, string, json.Number,
// []any, domain.RequestObject.
func Psycopg2Literal(value any) (string, error) {
	switch v := value.(type) {
	case nil:
		return "NULL", nil
	case bool:
		if v {
			return "true", nil
		}
		return "false", nil
	case string:
		if strings.ContainsRune(v, 0) {
			return "", &Psycopg2AdaptError{Text: "A string literal cannot contain NUL (0x00) characters."}
		}
		return "'" + strings.ReplaceAll(v, "'", "''") + "'", nil
	case json.Number:
		return psycopg2NumberLiteral(string(v))
	case []any:
		return psycopg2ListLiteral(v)
	case domain.RequestObject:
		return "", &Psycopg2AdaptError{Text: "can't adapt type 'dict'"}
	}
	return "", fmt.Errorf("psycopg2 literal: unexpected %T", value)
}

// psycopg2NumberLiteral adapts an int (arbitrary precision) or float token the way psycopg2 quotes numbers.
func psycopg2NumberLiteral(text string) (string, error) {
	if !strings.ContainsAny(text, ".eE") {
		integer, ok := new(big.Int).SetString(text, 10)
		if !ok {
			return "", fmt.Errorf("psycopg2 literal: invalid integer %q", text)
		}
		digits := integer.String()
		if integer.Sign() < 0 {
			// A leading space keeps a negative literal from reading as a "--" comment.
			return " " + digits, nil
		}
		return digits, nil
	}
	value, err := strconv.ParseFloat(text, 64)
	if err != nil {
		numErr, ok := err.(*strconv.NumError)
		if !ok || numErr.Err != strconv.ErrRange {
			return "", fmt.Errorf("psycopg2 literal: invalid float %q: %w", text, err)
		}
		// Overflow gives +/-Inf and underflow 0; both are the values Python's float() yields.
	}
	switch {
	case math.IsInf(value, 1):
		return "'Infinity'::float", nil
	case math.IsInf(value, -1):
		return "'-Infinity'::float", nil
	}
	repr := PythonFloatText(value)
	if strings.HasPrefix(repr, "-") {
		return " " + repr, nil
	}
	return repr, nil
}

// psycopg2ListLiteral adapts a Python list, following psycopg2's list_quote.
func psycopg2ListLiteral(items []any) (string, error) {
	if len(items) == 0 {
		return "'{}'", nil
	}
	literals := make([]string, len(items))
	allNulls := true
	for i, item := range items {
		literal, err := Psycopg2Literal(item)
		if err != nil {
			return "", err
		}
		if item == nil {
			literals[i] = literal
			continue
		}
		if _, nested := item.([]any); nested {
			switch {
			case strings.HasPrefix(literal, "A"):
				allNulls = false
			case literal == "'{}'":
				literal = "ARRAY[]"
				allNulls = false
			}
			literals[i] = literal
			continue
		}
		allNulls = false
		literals[i] = literal
	}
	if !allNulls {
		return "ARRAY[" + strings.Join(literals, ",") + "]", nil
	}
	// A list of NULLs (possibly nested) is written in array-input syntax instead.
	quoted := make([]string, len(literals))
	for i, literal := range literals {
		if len(literal) >= 2 && strings.HasPrefix(literal, "'") && strings.HasSuffix(literal, "'") {
			literal = literal[1 : len(literal)-1]
		}
		quoted[i] = literal
	}
	return "'{" + strings.Join(quoted, ",") + "}'", nil
}
