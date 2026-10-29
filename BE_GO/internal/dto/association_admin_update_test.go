package dto

import (
	"encoding/json"
	"testing"
	"time"
)

func TestDjangoFieldPrepMatchesPython(t *testing.T) {
	for _, tc := range []struct {
		value any
		want  int64
		ok    bool
	}{{" 12 ", 12, true}, {"1_0", 10, true}, {"+3", 3, true}, {"1__0", 0, false}, {"_1", 0, false}, {"1_", 0, false},
		{"1.5", 0, false}, {"", 0, false}, {json.Number("1.9"), 1, true}, {json.Number("-1.9"), -1, true}, {true, 1, true},
		{json.Number("1e400"), 0, false}, {nil, 0, false}, {[]any{}, 0, false}} {
		got, err := PythonIntPrep(tc.value)
		if (err == nil) != tc.ok || got != tc.want {
			t.Errorf("int(%#v) = %d %v", tc.value, got, err)
		}
	}
	for _, tc := range []struct {
		value    any
		want, ok bool
	}{{true, true, true}, {json.Number("1"), true, true}, {json.Number("0.0"), false, true}, {"True", true, true},
		{"t", true, true}, {"0", false, true}, {"true", false, false}, {"maybe", false, false}, {nil, false, false},
		{json.Number("2"), false, false}} {
		got, err := DjangoBooleanPrep(tc.value)
		if (err == nil) != tc.ok || got != tc.want {
			t.Errorf("BooleanField(%#v) = %v %v", tc.value, got, err)
		}
	}
	for value, want := range map[string]string{"2027-01-02T03:04:05Z": "2027-01-02T03:04:05Z", "2027-1-2": "2027-01-02T00:00:00Z",
		"2027-01-02 03:04:05+02:00": "2027-01-02T01:04:05Z", "2027-01-02": "2027-01-02T00:00:00Z"} {
		got, ok := DjangoDateTimePrep(value)
		if !ok || got.UTC().Format(time.RFC3339) != want {
			t.Errorf("DateTimeField(%q) = %v %v, want %s", value, got, ok, want)
		}
	}
	for _, value := range []string{"nope", "2027-02-30", ""} {
		if _, ok := DjangoDateTimePrep(value); ok {
			t.Errorf("DateTimeField(%q) accepted", value)
		}
	}
}
