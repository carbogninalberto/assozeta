package businessmcp

import (
	"encoding/json"
	"testing"
)

func TestValidateArgumentsBestMatch(t *testing.T) {
	for _, tc := range []struct{ tool, arguments, want string }{
		{"count_data", `{}`, "Input validation error: 'model_name' is a required property"},
		{"query_data", `{"model_name": 5, "fields": "x"}`, "Input validation error: 5 is not of type 'string'"},
		{"aggregate_data", `{"model_name": "Course", "aggregations": [{"function": "x", "field": "fee"}]}`,
			"Input validation error: 'x' is not one of ['count', 'sum', 'avg', 'min', 'max']"},
		{"aggregate_data", `{"model_name": "Course", "aggregations": [{"function": "sum"}]}`,
			"Input validation error: 'field' is a required property"},
		{"query_data", `{"model_name": "Course", "limit": 2.0}`, ""},
		{"query_data", `{"model_name": "Course", "limit": 2.5}`, "Input validation error: 2.5 is not of type 'integer'"},
		{"get_attendance_matrix", `{"export": 1}`, "Input validation error: 1 is not of type 'boolean'"},
	} {
		got, err := ValidateArguments(tc.tool, json.RawMessage(tc.arguments))
		if err != nil || got != tc.want {
			t.Errorf("%s %s: got %q %v, want %q", tc.tool, tc.arguments, got, err, tc.want)
		}
	}
}

func TestDeclarationsKeepDjangoOrder(t *testing.T) {
	tools := Declarations()
	if len(tools) != 15 || tools[0].Name != "get_schema" || tools[14].Name != "get_manual_gaps" {
		t.Fatalf("unexpected declarations: %d", len(tools))
	}
}
