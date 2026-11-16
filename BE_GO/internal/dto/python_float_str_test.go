package dto

import (
	"math"
	"testing"
)

// TestPythonFloatStr is str(float): the non-finite values Python prints, and
// the shortest round-trip repr of the finite ones.
func TestPythonFloatStr(t *testing.T) {
	for _, test := range []struct {
		value float64
		want  string
	}{
		{math.Inf(1), "inf"},
		{math.Inf(-1), "-inf"},
		{math.NaN(), "nan"},
		{0, "0.0"},
		{math.Copysign(0, -1), "-0.0"},
		{1.5, "1.5"},
		{1e16, "1e+16"},
		{-2.25, "-2.25"},
	} {
		if got := PythonFloatStr(test.value); got != test.want {
			t.Errorf("PythonFloatStr(%v) = %q, want %q", test.value, got, test.want)
		}
	}
}
