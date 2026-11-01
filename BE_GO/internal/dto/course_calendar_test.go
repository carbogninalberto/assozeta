package dto

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// Expectations confirmed with Python 3.13 and django.utils.dateparse.
func TestDjangoParseDateTime(t *testing.T) {
	cases := []struct {
		value  string
		want   string
		aware  bool
		result DjangoDateTime
	}{
		{"2026-11-02T17:00:00.000Z", "2026-11-02T17:00:00Z", true, DjangoDateTimeParsed},
		{"2026-11-17T18:00:00", "2026-11-17T18:00:00Z", false, DjangoDateTimeParsed},
		{"2026-12-24", "2026-12-24T00:00:00Z", false, DjangoDateTimeParsed},
		{"2026-11-04T08:30:00+01:00", "2026-11-04T07:30:00Z", true, DjangoDateTimeParsed},
		{"2026-11-17 18:00", "2026-11-17T18:00:00Z", false, DjangoDateTimeParsed},
		{"domani", "", false, DjangoDateTimeNone},
		{"2026-02-30T10:00:00", "", false, DjangoDateTimeValueError},
		{"2026-02-30 10:00", "", false, DjangoDateTimeValueError},
	}
	for _, tc := range cases {
		moment, aware, result := DjangoParseDateTime(tc.value)
		if result != tc.result || result == DjangoDateTimeParsed && (moment.Format(time.RFC3339) != tc.want || aware != tc.aware) {
			t.Errorf("%q: got %v %v %v", tc.value, moment, aware, result)
		}
	}
}

func TestCalendarEventDateTime(t *testing.T) {
	if moment, ok := CalendarEventDateTime("2026-12-01T21:00:00+02:00"); !ok || moment.Format(time.RFC3339) != "2026-12-01T19:00:00Z" {
		t.Fatalf("offset: %v %v", moment, ok)
	}
	for _, value := range []any{json.Number("5"), nil, "2026-02-30T10:00:00Z", "domani"} {
		if _, ok := CalendarEventDateTime(value); ok {
			t.Errorf("%v accepted", value)
		}
	}
}

func TestPythonInt(t *testing.T) {
	for value, want := range map[any]int64{" 1_000 ": 1000, json.Number("1.5"): 1, true: 1, "30": 30, json.Number("-2"): -2} {
		number, result := PythonInt(value)
		if result != PythonIntOK || number.Int64() != want {
			t.Errorf("%v: %v %v", value, number, result)
		}
	}
	for _, value := range []any{"1.5", "1__0", "_1", nil, domain.RequestObject{}} {
		if _, result := PythonInt(value); result != PythonIntInvalid {
			t.Errorf("%v: %v", value, result)
		}
	}
	if _, result := PythonInt(json.Number("1e400")); result != PythonIntOverflow {
		t.Errorf("1e400: %v", result)
	}
}

func TestPythonEqual(t *testing.T) {
	left := domain.RequestObject{{Name: "a", Value: json.Number("1")}, {Name: "b", Value: json.Number("2")}}
	right := domain.RequestObject{{Name: "b", Value: json.Number("2.0")}, {Name: "a", Value: true}}
	for _, pair := range [][2]any{{json.Number("2.0"), json.Number("2")}, {true, json.Number("1")}, {left, right}, {nil, nil},
		{[]any{"x"}, []any{"x"}}} {
		if !PythonEqual(pair[0], pair[1]) {
			t.Errorf("%v != %v", pair[0], pair[1])
		}
	}
	for _, pair := range [][2]any{{"1", json.Number("1")}, {nil, false}, {[]any{"x"}, []any{"x", "y"}}, {left, domain.RequestObject{}}} {
		if PythonEqual(pair[0], pair[1]) {
			t.Errorf("%v == %v", pair[0], pair[1])
		}
	}
}
