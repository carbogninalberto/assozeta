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

// TestPythonFloatReprPythonInputs covers float(str): Python's whitespace (not
// U+001C..U+001F), category-Nd digits mapped to ASCII, '_' only between two
// digits and no hexadecimal forms. Expected values were measured with
// CPython 3.13.16.
func TestPythonFloatReprPythonInputs(t *testing.T) {
	for _, tc := range []struct{ in, want string }{
		{"1_0", "10.0"}, {"1.5_0", "1.5"}, {"1e1_0", "10000000000.0"},
		{"1_000.0_1", "1000.01"}, {"1_0.0_1e1_0", "100100000000.0"},
		{"1_0.", "10.0"}, {"1.0_0", "1.0"}, {"1e0_1", "10.0"}, {"0_0", "0.0"}, {"00_1", "1.0"},
		{"1_0_0", "100.0"}, {"1_0e+1_0", "100000000000.0"}, {"1_0e-1_0", "1e-09"},
		{"١٢٣", "123.0"}, {"１２", "12.0"}, {"١_٢.٣", "12.3"}, {"１_２", "12.0"},
		{" 1_0 ", "10.0"}, {"\u00a01\u00a0", "1.0"}, {"\u20031\u2003", "1.0"}, {"\x0b1\x0c", "1.0"},
		{"1.e5", "100000.0"}, {".5e2", "50.0"}, {"-1_0", "-10.0"}, {"1e-999", "0.0"},
	} {
		got, ok := PythonFloatRepr(tc.in)
		if !ok || got != tc.want {
			t.Errorf("PythonFloatRepr(%q) = %q, %v; want %q", tc.in, got, ok, tc.want)
		}
	}
	for _, input := range []string{
		"_1", "1__0", "1_", "1._5", "._5", "_.5", "+_1", "-1_", "1e_1", "1_e1", "1e1_",
		"1_.0", "1._0", ".1_", "_.1", "._1", "1_0__0", "in_f", "i_nf", "0x1p-2", "0x1.8p1", "0x_1p-2",
		"1 0", "\x1c1\x1f", "1\u200b0", "\ufeff1", "1d5", "1,5", "1.2.3", "+-1", "-+1", ".", "+", "-",
		"nan", "NaN", "inf", "Infinity", "1e999",
	} {
		if got, ok := PythonFloatRepr(input); ok {
			t.Errorf("PythonFloatRepr(%q) = %q, want refusal", input, got)
		}
	}
	if got, ok := PythonFloatRepr(json.Number("1_0")); !ok || got != "10.0" {
		t.Errorf("PythonFloatRepr(json.Number(1_0)) = %q, %v; want 10.0", got, ok)
	}
	for _, input := range []any{json.Number("0x1p-2"), json.Number("1e999"), json.Number("1__0")} {
		if got, ok := PythonFloatRepr(input); ok {
			t.Errorf("PythonFloatRepr(%v) = %q, want refusal", input, got)
		}
	}
}
