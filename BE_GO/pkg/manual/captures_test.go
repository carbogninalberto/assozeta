package manual

import (
	"math"
	"testing"
)

func TestCapturesPythonFloatAndRound(t *testing.T) {
	for value, want := range map[float64]string{0: "0.0", .001: "0.001", 1e-4: "0.0001", 9.999e-5: "9.999e-05",
		4.8e-7: "4.8e-07", 1e16: "1e+16", 123.5: "123.5", math.Copysign(0, -1): "-0.0", math.Inf(1): "Infinity"} {
		if got := capturesFloat(value); got != want {
			t.Errorf("repr(%v) = %s, want %s", value, got, want)
		}
	}
	// round(0.5e-8, 8) and round(2.5e-8, 8) follow the binary value: 1e-08 and 2e-08.
	for value, want := range map[float64]float64{0.5e-8: 1e-8, 2.5e-8: 2e-8, 1.2345e-5: 1.234e-5, 0.000964506172839506: 0.00096451} {
		if got := capturesRound(value, 8); got != want {
			t.Errorf("round(%v, 8) = %v, want %v", value, got, want)
		}
	}
}

func TestCapturesPurePathAndJSON(t *testing.T) {
	for relative, want := range map[string]string{"a//./b.png": "captures/a/b.png", "/abs/x": "/abs/x", "//net/x": "//net/x",
		"///x": "/x", "a/../b": "captures/a/../b", "": "captures"} {
		if got := capturesPurePath("captures", relative); got != want {
			t.Errorf("Path('captures') / %q = %q, want %q", relative, got, want)
		}
	}
	value, err := capturesDecode([]byte(`{"b": 1, "a": [NaN, -0, 1E2, "è😀"], "b": 2}`))
	if err != nil {
		t.Fatal(err)
	}
	want := "{\n  \"b\": 2,\n  \"a\": [\n    NaN,\n    0,\n    100.0,\n    \"\\u00e8\\ud83d\\ude00\"\n  ]\n}"
	if got := capturesDumps(value); got != want {
		t.Errorf("dumps = %q", got)
	}
	for _, raw := range []string{`"\ud800"`, `[1,]`, `01`, `1.`, `{"a" 1}`, "\"\x01\""} {
		if _, err := capturesDecode([]byte(raw)); err == nil {
			t.Errorf("%q accepted", raw)
		}
	}
}
