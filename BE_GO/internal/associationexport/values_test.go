package associationexport

import (
	"strings"
	"testing"
	"time"
)

// Expected values are CPython 3.13 outputs (str(Decimal), repr(float),
// json.dumps(ensure_ascii=False), json.dumps(indent=2)).
func TestPythonValues(t *testing.T) {
	decimals := map[string]string{"10.50": "10.50", "0.00": "0.00", "0.0000001": "1E-7", "0.00000010": "1.0E-7",
		"123456789.123": "123456789.123", "-0.5": "-0.5", "100": "100", "0.000001": "0.000001", "NaN": "NaN"}
	for in, want := range decimals {
		if got, err := DecimalString(in); err != nil || got != want {
			t.Fatalf("DecimalString(%s) = %q, %v; want %q", in, got, err, want)
		}
	}
	floats := map[float64]string{1e16: "1e+16", 1e15: "1000000000000000.0", 0.0001: "0.0001", 0.00001: "1e-05", 1.5: "1.5",
		-2.0: "-2.0", 1.0 / 3: "0.3333333333333333", 123456789012345678.0: "1.2345678901234568e+17", 5e-324: "5e-324",
		1.7976931348623157e308: "1.7976931348623157e+308"}
	for in, want := range floats {
		if got := PythonFloatRepr(in); got != want {
			t.Fatalf("PythonFloatRepr(%v) = %q, want %q", in, got, want)
		}
	}
	encoded, err := AppendJSON(nil, Dict{{Key: "a", Value: "è \x7f\x01\"\\"}, {Key: "b", Value: []any{Float(1), nil, true}}})
	if err != nil || string(encoded) != "{\"a\": \"è \x7f\\u0001\\\"\\\\\", \"b\": [1.0, null, true]}" {
		t.Fatalf("AppendJSON: %s %v", encoded, err)
	}
	indented, err := AppendJSONIndent(nil, Dict{{Key: "k", Value: Dict{}}, {Key: "l", Value: []any{}},
		{Key: "m", Value: []any{1, Dict{{Key: "x", Value: nil}}}}}, 2)
	want := strings.Join([]string{"{", `  "k": {},`, `  "l": [],`, `  "m": [`, "    1,", "    {", `      "x": null`, "    }", "  ]", "}"}, "\n")
	if err != nil || string(indented) != want {
		t.Fatalf("AppendJSONIndent:\n%s", indented)
	}
	if got, _ := djangoTime("09:30:15.123456"); got != "09:30:15.123" {
		t.Fatalf("time %q", got)
	}
	if got, _ := djangoTime("09:30:15"); got != "09:30:15" {
		t.Fatalf("time %q", got)
	}
	stamp, err := parseTimestamp("2026-01-02 03:04:05.12+00")
	if err != nil || PythonISOFormat(stamp) != "2026-01-02T03:04:05.120000+00:00" {
		t.Fatalf("timestamp %v %v", stamp, err)
	}
	if PythonISOFormat(time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC)) != "2026-01-02T03:04:05+00:00" {
		t.Fatal("whole-second isoformat")
	}
	if _, err = parseTimestamp("infinity"); err == nil {
		t.Fatal("infinity accepted")
	}
	value, err := FromJSONB(`{"b": 1, "a": [1.50, 12345678901234567890, null, "x"]}`)
	if err != nil {
		t.Fatal(err)
	}
	if out, _ := AppendJSON(nil, value); string(out) != `{"b": 1, "a": [1.5, 12345678901234567890, null, "x"]}` {
		t.Fatalf("jsonb: %s", out)
	}
}

func TestPlanLoads(t *testing.T) {
	plan, err := LoadPlan()
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Models) != 64 || plan.Models[0].Name != "SportAssociation" || plan.Models[len(plan.Models)-1].Name != "Document" {
		t.Fatalf("plan models: %d", len(plan.Models))
	}
	e := &Exporter{Plan: plan, Association: "a", Owner: "o"}
	sql, args, err := e.bind(plan.Models[len(plan.Models)-1].Query.SQL, plan.Models[len(plan.Models)-1].Query.Params,
		map[string][]string{"documents": {"d1", "d2", "d3"}})
	if err != nil || !strings.Contains(sql, "IN ($1, $2, $3)") || len(args) != 3 {
		t.Fatalf("document IN list: %s %v %v", sql, args, err)
	}
}
