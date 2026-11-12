package dto

import (
	"encoding/json"
	"testing"
)

func TestPythonFloatReprUsesPythonNotation(t *testing.T) {
	for input, want := range map[any]string{
		"1E+2": "100.0", "-0": "-0.0", "0.01": "0.01", "1234567.89": "1234567.89", " 12.5 ": "12.5",
		"0.00001": "1e-05", "1e16": "1e+16", "123456789012345678": "1.2345678901234568e+17", "0.0001": "0.0001",
		json.Number("1e-7"): "1e-07", json.Number("5"): "5.0",
	} {
		got, ok := PythonFloatRepr(input)
		if !ok || got != want {
			t.Errorf("PythonFloatRepr(%v) = %q, %v; want %q", input, got, ok, want)
		}
	}
	for _, input := range []any{"abc", "nan", "inf", "1e999", true, nil} {
		if got, ok := PythonFloatRepr(input); ok {
			t.Errorf("PythonFloatRepr(%v) = %q, want refusal", input, got)
		}
	}
}
