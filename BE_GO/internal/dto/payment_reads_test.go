package dto

import (
	"testing"
	"time"
)

func TestPythonStrptimeYMD(t *testing.T) {
	cases := map[string]string{"2024/04/01": "2024-04-01", "2024/4/1": "2024-04-01", "2024/12/31": "2024-12-31", "2024/02/29": "2024-02-29",
		"2024/01/ 5": "2024-01-05", "２０２４/01/02": "2024-01-02"}
	for input, want := range cases {
		got, ok := PythonStrptimeYMD(input)
		if !ok || got.Format("2006-01-02") != want {
			t.Errorf("%q: got %v %v, want %s", input, got, ok, want)
		}
	}
	for _, input := range []string{"2023/02/29", "2024/13/01", "2024/00/10", "2024/01/32", "2024/01/1x", "24/01/01", "2024-01-01",
		"2024/01/01 ", "0000/01/01", ""} {
		if got, ok := PythonStrptimeYMD(input); ok {
			t.Errorf("%q: parsed as %v", input, got)
		}
	}
	if got, ok := PythonStrptimeDMYTime("02/07/2024"); !ok || !got.Equal(time.Date(2024, 7, 2, 0, 0, 0, 0, time.UTC)) {
		t.Errorf("DMY: %v %v", got, ok)
	}
}

func TestPaymentReadsJSONTruthy(t *testing.T) {
	text := func(value string) *string { return &value }
	for _, raw := range []string{"null", "{}", "[]", `""`, "false", "0", "0.0", "-0"} {
		if PaymentReadsJSONTruthy(text(raw)) {
			t.Errorf("%s is falsy in Python", raw)
		}
	}
	for _, raw := range []string{`{"a": 1}`, "[0]", `"x"`, "true", "1.5", "-2"} {
		if !PaymentReadsJSONTruthy(text(raw)) {
			t.Errorf("%s is truthy in Python", raw)
		}
	}
	if PaymentReadsJSONTruthy(nil) {
		t.Error("None is falsy")
	}
}
