package manualmcp

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"math/big"
	"sort"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

const validationPrefix = "Input validation error: "

// Validate checks arguments against the authored schema for name. Django uses
// jsonschema.validate, which reports these schema errors in precedence order:
// missing required properties, unexpected top-level properties, then property
// validation failures. Property failures follow the schema's property order.
func Validate(name string, arguments map[string]any) error {
	switch name {
	case "search_manual":
		if _, present := arguments["query"]; !present {
			return validationError("'query' is a required property")
		}
		if err := validateAdditionalProperties(arguments, "query", "limit"); err != nil {
			return err
		}
		if err := validateDefinitionsProperty(arguments, "query", func(value any) error {
			return validateString(value, 2000)
		}); err != nil {
			return err
		}
		if err := validateDefinitionsProperty(arguments, "limit", func(value any) error {
			return validateInteger(value, 1, 10)
		}); err != nil {
			return err
		}
		return nil
	case "get_manual_section", "get_manual_evidence":
		if _, present := arguments["section_id"]; !present {
			return validationError("'section_id' is a required property")
		}
		if err := validateAdditionalProperties(arguments, "section_id"); err != nil {
			return err
		}
		if err := validateDefinitionsProperty(arguments, "section_id", func(value any) error {
			return validateString(value, 0)
		}); err != nil {
			return err
		}
		return nil
	case "get_manual_gaps":
		if err := validateAdditionalProperties(arguments, "page"); err != nil {
			return err
		}
		if err := validateDefinitionsProperty(arguments, "page", func(value any) error {
			return validateString(value, 256)
		}); err != nil {
			return err
		}
		return nil
	default:
		// Unknown tools belong to the transport, whose Django-compatible body
		// is not an input-schema error.
		return nil
	}
}

// ValidateJSON checks raw JSON arguments while preserving insertion order in
// nested objects. Django's json.loads produces dictionaries whose repr keeps
// the first insertion position and last value for duplicate keys; jsonschema
// then embeds that repr in malformed-object errors.
func ValidateJSON(name string, arguments json.RawMessage) error {
	value, err := decodeOrderedJSON(arguments)
	if err != nil {
		return fmt.Errorf("%sinvalid JSON arguments: %w", validationPrefix, err)
	}
	object, ok := value.(orderedObject)
	if !ok {
		return validationError("arguments must be a JSON object")
	}

	properties := make(map[string]any, len(object.keys))
	for _, key := range object.keys {
		properties[key] = object.values[key]
	}
	return Validate(name, properties)
}

// orderedObject is the insertion-ordered equivalent of a Python dictionary.
// It exists only while validating raw JSON; callers of Validate continue to
// use ordinary maps and receive the documented sorted-key fallback.
type orderedObject struct {
	keys   []string
	values map[string]any
}

func decodeOrderedJSON(raw json.RawMessage) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()

	value, err := decodeOrderedJSONValue(decoder)
	if err != nil {
		return nil, err
	}
	if _, err := decoder.Token(); !errors.Is(err, io.EOF) {
		if err == nil {
			return nil, errors.New("multiple JSON values")
		}
		return nil, err
	}
	return value, nil
}

func decodeOrderedJSONValue(decoder *json.Decoder) (any, error) {
	token, err := decoder.Token()
	if err != nil {
		return nil, err
	}

	switch typed := token.(type) {
	case json.Delim:
		switch typed {
		case '{':
			object := orderedObject{values: make(map[string]any)}
			for decoder.More() {
				keyToken, err := decoder.Token()
				if err != nil {
					return nil, err
				}
				key, ok := keyToken.(string)
				if !ok {
					return nil, fmt.Errorf("object key is %T, want string", keyToken)
				}
				value, err := decodeOrderedJSONValue(decoder)
				if err != nil {
					return nil, err
				}
				if _, present := object.values[key]; !present {
					object.keys = append(object.keys, key)
				}
				object.values[key] = value
			}
			end, err := decoder.Token()
			if err != nil {
				return nil, err
			}
			if end != json.Delim('}') {
				return nil, fmt.Errorf("object ended with %v, want '}'", end)
			}
			return object, nil
		case '[':
			var values []any
			for decoder.More() {
				value, err := decodeOrderedJSONValue(decoder)
				if err != nil {
					return nil, err
				}
				values = append(values, value)
			}
			end, err := decoder.Token()
			if err != nil {
				return nil, err
			}
			if end != json.Delim(']') {
				return nil, fmt.Errorf("array ended with %v, want ']'", end)
			}
			return values, nil
		default:
			return nil, fmt.Errorf("unexpected JSON delimiter %q", typed)
		}
	case nil, bool, string, json.Number:
		return typed, nil
	default:
		return nil, fmt.Errorf("unexpected JSON token %T", token)
	}
}

func validateDefinitionsProperty(arguments map[string]any, property string, validate func(any) error) error {
	if value, present := arguments[property]; present {
		return validate(value)
	}
	return nil
}

func validateString(value any, maxLength int) error {
	text, ok := value.(string)
	if !ok {
		return validationError("%s is not of type 'string'", pythonRepresentation(value))
	}
	if maxLength > 0 && utf8.RuneCountInString(text) > maxLength {
		return validationError("%s is too long", pythonRepresentation(text))
	}
	return nil
}

func validateInteger(value any, minimum, maximum int64) error {
	number, ok := integerValue(value)
	if !ok {
		return validationError("%s is not of type 'integer'", pythonRepresentation(value))
	}
	lower := big.NewRat(minimum, 1)
	upper := big.NewRat(maximum, 1)
	if number.Cmp(lower) < 0 {
		return validationError("%s is less than the minimum of %d", pythonRepresentation(value), minimum)
	}
	if number.Cmp(upper) > 0 {
		return validationError("%s is greater than the maximum of %d", pythonRepresentation(value), maximum)
	}
	return nil
}

func integerValue(value any) (*big.Rat, bool) {
	number := new(big.Rat)
	switch typed := value.(type) {
	case json.Number:
		raw := string(typed)
		if !json.Valid([]byte(raw)) {
			return nil, false
		}
		if !strings.ContainsAny(raw, ".eE") {
			if number.SetString(raw); number.IsInt() {
				return number, true
			}
			return nil, false
		}
		// Python's JSON parser converts decimal and exponent forms to float.
		// The MCP SDK checks that resulting value for integrality, including
		// its binary rounding and overflow to infinity.
		parsed, err := strconv.ParseFloat(raw, 64)
		if err != nil && !errors.Is(err, strconv.ErrRange) {
			return nil, false
		}
		return integerValue(parsed)
	case float64:
		if math.IsNaN(typed) || math.IsInf(typed, 0) || math.Trunc(typed) != typed {
			return nil, false
		}
		return number.SetFloat64(typed), true
	case int:
		return number.SetInt64(int64(typed)), true
	case int8:
		return number.SetInt64(int64(typed)), true
	case int16:
		return number.SetInt64(int64(typed)), true
	case int32:
		return number.SetInt64(int64(typed)), true
	case int64:
		return number.SetInt64(typed), true
	case uint:
		return number.SetString(strconv.FormatUint(uint64(typed), 10))
	case uint8:
		return number.SetInt64(int64(typed)), true
	case uint16:
		return number.SetInt64(int64(typed)), true
	case uint32:
		return number.SetInt64(int64(typed)), true
	case uint64:
		return number.SetString(strconv.FormatUint(typed, 10))
	default:
		// bool is intentionally not an integer, matching jsonschema.
		return nil, false
	}
}

func validateAdditionalProperties(arguments map[string]any, properties ...string) error {
	known := make(map[string]struct{}, len(properties))
	for _, property := range properties {
		known[property] = struct{}{}
	}
	var additional []string
	for property := range arguments {
		if _, ok := known[property]; !ok {
			additional = append(additional, property)
		}
	}
	if len(additional) == 0 {
		return nil
	}
	sort.Strings(additional)
	quoted := make([]string, len(additional))
	for index, property := range additional {
		quoted[index] = pythonRepresentation(property)
	}
	verb := "was"
	if len(additional) > 1 {
		verb = "were"
	}
	return validationError("Additional properties are not allowed (%s %s unexpected)", strings.Join(quoted, ", "), verb)
}

func validationError(format string, values ...any) error {
	return fmt.Errorf("%s%s", validationPrefix, fmt.Sprintf(format, values...))
}

func pythonRepresentation(value any) string {
	switch typed := value.(type) {
	case nil:
		return "None"
	case bool:
		if typed {
			return "True"
		}
		return "False"
	case string:
		return pythonString(typed)
	case json.Number:
		return pythonNumber(string(typed))
	case float64:
		return pythonFloat(typed)
	case float32:
		return pythonFloat(float64(typed))
	case int:
		return strconv.FormatInt(int64(typed), 10)
	case int8:
		return strconv.FormatInt(int64(typed), 10)
	case int16:
		return strconv.FormatInt(int64(typed), 10)
	case int32:
		return strconv.FormatInt(int64(typed), 10)
	case int64:
		return strconv.FormatInt(typed, 10)
	case uint:
		return strconv.FormatUint(uint64(typed), 10)
	case uint8:
		return strconv.FormatUint(uint64(typed), 10)
	case uint16:
		return strconv.FormatUint(uint64(typed), 10)
	case uint32:
		return strconv.FormatUint(uint64(typed), 10)
	case uint64:
		return strconv.FormatUint(typed, 10)
	case []any:
		items := make([]string, len(typed))
		for index, item := range typed {
			items[index] = pythonRepresentation(item)
		}
		return "[" + strings.Join(items, ", ") + "]"
	case orderedObject:
		items := make([]string, len(typed.keys))
		for index, key := range typed.keys {
			items[index] = pythonRepresentation(key) + ": " + pythonRepresentation(typed.values[key])
		}
		return "{" + strings.Join(items, ", ") + "}"
	case map[string]any:
		// A Go map has already lost JSON insertion order. Keep the fallback
		// deterministic with sorted keys, but this cannot exactly reproduce
		// Python dict repr ordering for a nested object supplied in JSON.
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		items := make([]string, len(keys))
		for index, key := range keys {
			items[index] = pythonRepresentation(key) + ": " + pythonRepresentation(typed[key])
		}
		return "{" + strings.Join(items, ", ") + "}"
	default:
		return fmt.Sprint(value)
	}
}

func pythonNumber(raw string) string {
	if !json.Valid([]byte(raw)) {
		return raw
	}
	if !strings.ContainsAny(raw, ".eE") {
		if raw == "-0" {
			return "0"
		}
		return raw
	}
	value, err := strconv.ParseFloat(raw, 64)
	if err != nil && !errors.Is(err, strconv.ErrRange) {
		return raw
	}
	return pythonFloat(value)
}

func pythonFloat(value float64) string {
	switch {
	case math.IsNaN(value):
		return "nan"
	case math.IsInf(value, 1):
		return "inf"
	case math.IsInf(value, -1):
		return "-inf"
	}
	if absolute := math.Abs(value); absolute != 0 && (absolute < 1e-4 || absolute >= 1e16) {
		return strconv.FormatFloat(value, 'e', -1, 64)
	}
	text := strconv.FormatFloat(value, 'f', -1, 64)
	if !strings.Contains(text, ".") {
		text += ".0"
	}
	return text
}

func pythonString(value string) string {
	quote := byte('\'')
	if strings.Contains(value, "'") && !strings.Contains(value, "\"") {
		quote = '"'
	}
	var output strings.Builder
	output.WriteByte(quote)
	for _, character := range value {
		switch character {
		case '\\':
			output.WriteString(`\\`)
		case '\t':
			output.WriteString(`\t`)
		case '\n':
			output.WriteString(`\n`)
		case '\r':
			output.WriteString(`\r`)
		default:
			switch {
			case character == rune(quote):
				output.WriteByte('\\')
				output.WriteRune(character)
			case unicode.IsPrint(character):
				output.WriteRune(character)
			case character <= 0xff:
				fmt.Fprintf(&output, `\x%02x`, character)
			case character <= 0xffff:
				fmt.Fprintf(&output, `\u%04x`, character)
			default:
				fmt.Fprintf(&output, `\U%08x`, character)
			}
		}
	}
	output.WriteByte(quote)
	return output.String()
}
