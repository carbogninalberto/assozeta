package dto

import (
	"encoding/json"
	"strings"
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

// TestDRFDecimalPythonInputs covers what DRF hands to decimal.Decimal: Python's
// str.strip() whitespace, every underscore removed before the grammar and any
// category-Nd digit mapped to ASCII. Expected values were measured with
// CPython 3.13.16 (Decimal(...) then DRF's precision checks, max_digits 9 and
// 2 places).
func TestDRFDecimalPythonInputs(t *testing.T) {
	for _, tc := range []struct {
		in         string
		want, code string
	}{
		{"_1", "1.00", ""}, {"1__0", "10.00", ""}, {"1_", "1.00", ""}, {"1._5", "1.50", ""},
		{"._5", "0.50", ""}, {"_.5", "0.50", ""}, {"+_1", "1.00", ""}, {"_+1", "1.00", ""},
		{"1e_5", "100000.00", ""}, {"1e5_", "100000.00", ""}, {"1_e5", "100000.00", ""},
		{"1_0", "10.00", ""}, {"1_000.0_1", "1000.01", ""}, {"1_0.0_1e1", "100.10", ""},
		{"١٢٣", "123.00", ""}, {"١٢٣.٤٥", "123.45", ""}, {"١_٢", "12.00", ""}, {"１２", "12.00", ""},
		{"١e٢", "100.00", ""}, {"１_２.３", "12.30", ""},
		{" 1 ", "1.00", ""}, {"\u00a01", "1.00", ""}, {"\x1c1\x1f", "1.00", ""}, {"\u20031\u2003", "1.00", ""},
		{"_", "", "number"}, {"__", "", "number"}, {"1 0", "", "number"}, {"1\x00", "", "number"},
		{"+-1", "", "number"}, {"-+1", "", "number"}, {"1,5", "", "number"}, {"1.2.3", "", "number"},
		{"0x1p-2", "", "number"}, {"1d5", "", "number"}, {"\u00b2", "", "number"}, {"1\u20030", "", "number"},
		{"I_nf", "", "number"}, {"N_aN", "", "number"}, {"s_nan", "", "number"}, {"nan1_2", "", "number"},
		{"in_f", "", "number"}, {"+inf", "", "number"}, {"1e999999999999999999999", "", "number"},
		{"1e999", "", "max-digits:9"},
		{strings.Repeat("1", 1001), "", "max-string-length"},
		{strings.Repeat("1", 1000) + "_", "", "max-string-length"},
		{strings.Repeat("_", 1000), "", "number"},
	} {
		got, code := DRFDecimal(tc.in, 9, 2)
		if code != tc.code || got != tc.want {
			t.Errorf("DRFDecimal(%q) = %q, %q; want %q, %q", tc.in, got, code, tc.want, tc.code)
		}
	}
}
