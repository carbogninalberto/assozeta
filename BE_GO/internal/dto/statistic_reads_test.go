package dto

import (
	"encoding/json"
	"strings"
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
		"٢٠26-11-18T1٠:0٠:0٠.000Z": "2026-11-18T10:00:00Z", // the fixture's literal + strftime seed
	}
	for input, want := range cases {
		got, ok := StrptimeEventStart(input)
		if (want == "") == ok || (ok && got.Format(time.RFC3339Nano) != want) {
			t.Errorf("%q: got %v %v, want %q", input, got, ok, want)
		}
	}
}

// CPython's _strptime uses \d for %Y, for the second digit of %d/%H/%M/%S and for int(), so a
// start mixing scripts names the same instant; %m's alternatives and %f stay ASCII.
func TestStrptimeEventStartUnicodeDigits(t *testing.T) {
	want, ok := StrptimeEventStart("2026-11-20T10:00:00.000Z")
	if !ok {
		t.Fatal("the ASCII start must parse")
	}
	got, ok := StrptimeEventStart("٢٠٢٦-11-2٠T1٠:0٠:0٠.000Z")
	if !ok || !got.Equal(want) {
		t.Errorf("Unicode digits: got %v %v, want %v", got, ok, want)
	}
	if _, ok := StrptimeEventStart("2026-١١-20T10:00:00.000Z"); ok {
		t.Error("a Unicode month is a ValueError in CPython")
	}
	if _, ok := StrptimeEventStart("2026-11-20T10:٠٠:00.000Z"); ok {
		t.Error("CPython's first minute digit is ASCII only")
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

// Expected values were produced by CPython 3.13:
// datetime.strptime(text, '%Y-%m-%d %H:%M:%S%z').astimezone(timezone.utc).
// CPython's _strptime builds %Y, %H, %M, %S and the digits of %z with \d, but
// %m, the first digit of %d/%H/%M and the second digit of %z's minute with
// ASCII classes: a month of another script is a ValueError.
func TestStrptimeLessonDateMatchesCPython(t *testing.T) {
	cases := map[string]string{
		"2026-11-10 10:00:00+0000": "2026-11-10T10:00:00Z",
		// Unicode digits where CPython's \d alternatives allow them.
		"٢٠٢٦-11-10 10:00:00+0000":  "2026-11-10T10:00:00Z",
		"٢٠26-11-10 10:00:00+0000":  "2026-11-10T10:00:00Z",
		"٢٠26-11-11 1٠:0٠:0٠+0000":  "2026-11-11T10:00:00Z", // the fixture's registry form
		"2026-11-10 1٠:0٠:0٠+0000":  "2026-11-10T10:00:00Z",
		"2026-11-1٠ 10:00:00+0000":  "2026-11-10T10:00:00Z",
		"2026-11-10 10:00:00+0٠:0٠": "2026-11-10T10:00:00Z",
		"2026-11-10 10:00:00+٠1:00": "2026-11-10T09:00:00Z",
		"2026-11-10 10:00:00+0١:00": "2026-11-10T09:00:00Z",
		"2026-11-10 10:00:00+٠١:٠٠": "",
		"2026-11-10 10:٠٠:00+0000":  "",
		"2026-11-10 ١0:00:00+0000":  "",
		"٢٠٢٦-١١-١٠ ١٠:٠٠:٠٠+0000":  "",
		"２０２６-１１-１０ １０:００:００+００００":  "",
		// %z forms and their bounds.
		"2026-11-10 10:00:00Z":                "2026-11-10T10:00:00Z",
		"2026-11-10 10:00:00z":                "",
		"2026-11-10 10:00:00+01:00":           "2026-11-10T09:00:00Z",
		"2026-11-10 10:00:00+0100":            "2026-11-10T09:00:00Z",
		"2026-11-10 10:00:00-01:00":           "2026-11-10T11:00:00Z",
		"2026-11-10 10:00:00+01:00:30":        "2026-11-10T08:59:30Z",
		"2026-11-10 10:00:00+01:00:30.5":      "2026-11-10T08:59:29.5Z",
		"2026-11-10 10:00:00+010030.25":       "2026-11-10T08:59:29.75Z",
		"2026-11-10 10:00:00+01:00:30.123456": "2026-11-10T08:59:29.876544Z",
		"2026-11-10 10:00:00+23:59:59.999999": "2026-11-09T10:00:00.000001Z",
		"2026-11-10 10:00:00+24:00":           "",
		"2026-11-10 10:00:00+01:0":            "",
		"2026-11-10 10:00:00+0160":            "",
		"2026-11-10 10:00:00+01:00:60":        "",
		"2026-11-10 10:00:00+01:0030":         "",
		"2026-11-10 10:00:00+0100:30":         "",
		"2026-11-10 10:00:00+01:00:3":         "",
		// Field ranges and the whitespace separator (\s+ in the format).
		"2026-11-10 10:00:00":           "",
		"2026-02-29 10:00:00+0000":      "",
		"2024-02-29 10:00:00+0000":      "2024-02-29T10:00:00Z",
		"2026-11-10 10:00:60+0000":      "",
		"2026-11-10 24:00:00+0000":      "",
		"2026-11-10  5:00:00+0000":      "2026-11-10T05:00:00Z",
		"2026-11-10 5:00:00+0000":       "2026-11-10T05:00:00Z",
		"2026-11-10\t05:00:00+0000":     "2026-11-10T05:00:00Z",
		"2026-11-10\u00a005:00:00+0000": "2026-11-10T05:00:00Z",
	}
	for input, want := range cases {
		got, ok := StrptimeLessonDate(input)
		if (want == "") == ok || (ok && got.UTC().Format(time.RFC3339Nano) != want) {
			t.Errorf("%q: got %v %v, want %q", input, got, ok, want)
		}
	}
}

// jsonb stores numbers without an exponent, and json.loads reads a fraction as
// a float: json.dumps then writes its repr, so an underflow becomes 0.0 (and
// -0.0 with a negative token), not the stored digits.
func TestPythonJSONDumpsUnderflow(t *testing.T) {
	zeros := strings.Repeat("0", 400)
	if got := string(PythonJSONDumpsCompact(json.Number("0." + zeros + "1"))); got != "0.0" {
		t.Errorf("positive underflow: %s", got)
	}
	if got := string(PythonJSONDumpsCompact(json.Number("-0." + zeros + "1"))); got != "-0.0" {
		t.Errorf("negative underflow: %s", got)
	}
	// The opposite side of the range: the same fraction with 400 integer zeros
	// is inf for json.loads, which the widget refuses before rendering.
	raw := `{"lessons_left": 1, "big": 1` + zeros + `.5}`
	value, err := ParseReferenceJSON([]byte(raw))
	if err != nil {
		t.Fatalf("overflow fraction: %v", err)
	}
	if PythonJSONFinite(value) {
		t.Fatal("a fraction beyond the double range must not be finite")
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
