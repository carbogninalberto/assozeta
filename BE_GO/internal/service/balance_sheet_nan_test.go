package service

import (
	"math"
	"testing"
)

// TestBalanceSheetCommaNonFinite pins f"{x:.2f}".replace(".", ",") for the
// non-finite floats the stored amounts can hold.
func TestBalanceSheetCommaNonFinite(t *testing.T) {
	for _, test := range []struct {
		value float64
		want  string
	}{
		{math.NaN(), "nan"},
		{math.Inf(1), "inf"},
		{math.Inf(-1), "-inf"},
		{1234.5, "1234,50"},
		{-1.5, "-1,50"},
	} {
		if got := balanceSheetComma(test.value); got != test.want {
			t.Errorf("balanceSheetComma(%v) = %q, want %q", test.value, got, test.want)
		}
	}
}
