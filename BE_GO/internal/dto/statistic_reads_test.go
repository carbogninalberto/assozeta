package dto

import (
	"encoding/json"
	"testing"
	"time"
)

// Expected values were produced by CPython 3.13 (the production runtime).
func TestPythonFloatSumIsCompensated(t *testing.T) {
	cases := []struct {
		values []float64
		want   string
	}{
		{[]float64{0.0, 0.1, 0.3, 0.1, 0.1, 0.3, 0.2}, "1.1"},
		{[]float64{1e100, 1.0, -1e100}, "1.0"},
		{[]float64{0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1}, "1.0"},
		{nil, "0.0"},
	}
	for _, c := range cases {
		if got := pythonFloatRepr(PythonFloatSum(c.values)); got != c.want {
			t.Errorf("%v: got %s, want %s", c.values, got, c.want)
		}
	}
}

func TestStrptimeEventStart(t *testing.T) {
	cases := map[string]string{
		"2026-11-03T10:00:00.000Z": "2026-11-03T10:00:00Z", "2026-11-03t10:00:00.5z": "2026-11-03T10:00:00.5Z",
		"2026-2-3T1:2:3.123456Z": "2026-02-03T01:02:03.123456Z", "2026-11- 3T10:00:00.0Z": "2026-11-03T10:00:00Z",
		"2026-02-30T10:00:00.0Z": "", "2026-11-03T10:00:60.0Z": "", "2026-11-03T10:00:00Z": "",
		"2026-11-03T10:00:00.1234567Z": "", "0000-01-01T00:00:00.0Z": "",
	}
	for input, want := range cases {
		got, ok := StrptimeEventStart(input)
		if (want == "") == ok || (ok && got.Format(time.RFC3339Nano) != want) {
			t.Errorf("%q: got %v %v, want %q", input, got, ok, want)
		}
	}
}

func TestStrptimeLessonDate(t *testing.T) {
	cases := map[string]string{
		"2026-10-27 21:00:00+0000": "2026-10-27T21:00:00Z", "2026-10-27 21:00:00+01:30": "2026-10-27T19:30:00Z",
		"2026-10-27  21:00:00-0130": "2026-10-27T22:30:00Z", "2026-10-27 21:00:00Z": "2026-10-27T21:00:00Z",
		"2026-10-27 21:00:00+00:00:30.5": "2026-10-27T20:59:29.5Z", "2026-10-27 21:00:00+000000.25": "2026-10-27T20:59:59.75Z",
		"2026-10-27 21:00:00+2400": "", "2026-10-27 21:00:00+00:0000": "", "2026-10-27 21:00:00z": "", "2026-10-27 21:00:00": "",
	}
	for input, want := range cases {
		got, ok := StrptimeLessonDate(input)
		if (want == "") == ok || (ok && got.UTC().Format(time.RFC3339Nano) != want) {
			t.Errorf("%q: got %v %v, want %q", input, got, ok, want)
		}
	}
}

func TestDecimalRendering(t *testing.T) {
	zero, value := "0.00", "12.50"
	if string(DecimalOrZero(&zero)) != "0" || string(DecimalOrZero(nil)) != "0" || string(DecimalOrZero(&value)) != "12.5" {
		t.Error("Decimal or 0")
	}
	if string(DecimalNumber(nil)) != "null" || string(DecimalNumber(&zero)) != "0.0" {
		t.Error("float(Decimal)")
	}
	if StrptimeUnicodeDigits("2026-10-27") || !StrptimeUnicodeDigits("٢٠٢٦-10-27") {
		t.Error("unicode digits")
	}
	if PythonJSONFinite(json.Number("1e400")) || !PythonJSONFinite(json.Number("1e300")) {
		t.Error("finite")
	}
}
