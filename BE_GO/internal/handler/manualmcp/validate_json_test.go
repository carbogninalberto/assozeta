package manualmcp

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestValidateJSONMatchesPythonNestedObjectRepresentation(t *testing.T) {
	tests := []struct {
		name string
		raw  json.RawMessage
		want string
	}{
		{
			name: "object insertion order",
			raw:  json.RawMessage(`{"query":{"z":1,"a":2}}`),
			want: "Input validation error: {'z': 1, 'a': 2} is not of type 'string'",
		},
		{
			name: "nested objects and arrays",
			raw:  json.RawMessage(`{"query":{"outer":{"z":1,"a":2},"items":[{"m":3,"b":4},[5,{"q":"v"}]]}}`),
			want: "Input validation error: {'outer': {'z': 1, 'a': 2}, 'items': [{'m': 3, 'b': 4}, [5, {'q': 'v'}]]} is not of type 'string'",
		},
		{
			name: "unicode and escapes",
			raw:  json.RawMessage(`{"query":{"z":"caff\u00e8 \ud83d\ude00","a":"line\nquote\u0027 slash\/ tab\t ctrl\u0001 bell\u0007"}}`),
			want: "Input validation error: {'z': 'caffè 😀', 'a': \"line\\nquote' slash/ tab\\t ctrl\\x01 bell\\x07\"} is not of type 'string'",
		},
		{
			name: "duplicate key keeps first position and last value",
			raw:  json.RawMessage(`{"query":{"first":1,"dup":"old","middle":2,"dup":"new","last":3}}`),
			want: "Input validation error: {'first': 1, 'dup': 'new', 'middle': 2, 'last': 3} is not of type 'string'",
		},
		{
			name: "duplicate nested object value",
			raw:  json.RawMessage(`{"query":[{"first":1,"dup":{"b":2,"a":1},"dup":{"d":4,"c":3},"last":5}]}`),
			want: "Input validation error: [{'first': 1, 'dup': {'d': 4, 'c': 3}, 'last': 5}] is not of type 'string'",
		},
		{
			name: "escaped object keys",
			raw:  json.RawMessage(`{"query":{"z\nkey":"v","a\u0027key":"w"}}`),
			want: "Input validation error: {'z\\nkey': 'v', \"a'key\": 'w'} is not of type 'string'",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			err := ValidateJSON("search_manual", tc.raw)
			if err == nil || err.Error() != tc.want {
				t.Fatalf("ValidateJSON() = %v, want %q", err, tc.want)
			}
		})
	}
}

func TestValidateJSONUsesJSONNumberSemantics(t *testing.T) {
	tests := []struct {
		name    string
		raw     json.RawMessage
		wantErr string
	}{
		{name: "plain integer", raw: json.RawMessage(`{"query":"fixture","limit":2}`)},
		{name: "integral decimal", raw: json.RawMessage(`{"query":"fixture","limit":2.0}`)},
		{name: "integral exponent", raw: json.RawMessage(`{"query":"fixture","limit":1e1}`)},
		{
			name:    "fractional number",
			raw:     json.RawMessage(`{"query":"fixture","limit":2.8}`),
			wantErr: "Input validation error: 2.8 is not of type 'integer'",
		},
		{
			name:    "Python float rounding",
			raw:     json.RawMessage(`{"query":"fixture","limit":9007199254740993.0}`),
			wantErr: "Input validation error: 9007199254740992.0 is greater than the maximum of 10",
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			err := ValidateJSON("search_manual", tc.raw)
			if tc.wantErr == "" {
				if err != nil {
					t.Fatalf("ValidateJSON() = %v, want nil", err)
				}
				return
			}
			if err == nil || err.Error() != tc.wantErr {
				t.Fatalf("ValidateJSON() = %v, want %q", err, tc.wantErr)
			}
		})
	}
}

func TestValidateJSONRejectsInvalidAndNonObjectTopLevel(t *testing.T) {
	tests := []struct {
		name        string
		raw         json.RawMessage
		wantContain string
	}{
		{name: "syntax error", raw: json.RawMessage(`{`), wantContain: "invalid JSON arguments"},
		{name: "trailing value", raw: json.RawMessage(`{} {}`), wantContain: "invalid JSON arguments"},
		{name: "null", raw: json.RawMessage(`null`), wantContain: "arguments must be a JSON object"},
		{name: "array", raw: json.RawMessage(`[]`), wantContain: "arguments must be a JSON object"},
		{name: "string", raw: json.RawMessage(`"fixture"`), wantContain: "arguments must be a JSON object"},
		{name: "number", raw: json.RawMessage(`2`), wantContain: "arguments must be a JSON object"},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			err := ValidateJSON("search_manual", tc.raw)
			if err == nil || !strings.Contains(err.Error(), tc.wantContain) {
				t.Fatalf("ValidateJSON() = %v, want error containing %q", err, tc.wantContain)
			}
		})
	}
}
