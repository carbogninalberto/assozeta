package dto

import (
	"encoding/json"
	"testing"
)

func TestDRFIntegerAndDecimal(t *testing.T) {
	for _, tc := range []struct {
		in   any
		want string
		code string
	}{{json.Number("20"), "20", ""}, {"20.0", "20", ""}, {" 7 ", "7", ""}, {json.Number("5.0"), "5", ""}, {json.Number("1.5"), "", "integer"},
		{true, "", "integer"}, {"1e3", "", "integer"}, {"1_0", "10", ""}} {
		got, code := DRFInteger(tc.in)
		if code != tc.code || (code == "" && got.String() != tc.want) {
			t.Errorf("integer %v: %v %q", tc.in, got, code)
		}
	}
	for _, tc := range []struct {
		in         any
		want, code string
	}{{json.Number("75"), "75.00", ""}, {json.Number("1.0"), "1.00", ""}, {"12.5", "12.50", ""}, {"-1", "-1.00", ""}, {"0.001", "", "max-decimal-places:2"},
		{json.Number("12345678"), "", "max-whole-digits:7"}, {"1234567.891", "", "max-digits:9"}, {"abc", "", "number"}, {"NaN", "", "number"},
		{"1E+2", "100.00", ""}, {".5", "0.50", ""}, {"5.", "5.00", ""}, {"007.10", "7.10", ""}, {"0.00", "0.00", ""}} {
		got, code := DRFDecimal(tc.in, 9, 2)
		if code != tc.code || got != tc.want {
			t.Errorf("decimal %v: %q %q", tc.in, got, code)
		}
	}
}
