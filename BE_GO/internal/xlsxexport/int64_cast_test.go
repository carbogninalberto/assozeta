package xlsxexport

import (
	"math"
	"testing"
)

// TestExcelUtilsCoerceInt64Bounds pins get_excel_base64's astype('Int64') for
// a float64 column (an int next to None) at the int64 bounds. Expected
// values: pandas 2.2.3 / numpy on arm64 (macOS and linux/arm64 alike) and the
// amd64 cast, whose out-of-range result is int64 min:
//
//	[-2**63, None]       -> Int64 [-9223372036854775808, <NA>]   (every platform)
//	[-2**63 - 1, None]   -> Int64 [-9223372036854775808, <NA>]   (the float is -2**63)
//	[-2**63 - 5000, None] -> stays float64
//	[2**63, None]        -> arm64 Int64 [9223372036854775807, <NA>]; amd64 stays float64
//	[2**63 + 2000, None] -> stays float64
func TestExcelUtilsCoerceInt64Bounds(t *testing.T) {
	saved := float64ToInt64Saturates
	defer func() { float64ToInt64Saturates = saved }()
	column := func(digits string) frameColumn {
		inferred, err := inferColumn([]Value{BigIntValue(digits), NoneValue()})
		if err != nil {
			t.Fatalf("inferColumn(%s): %v", digits, err)
		}
		return inferred
	}
	cases := []struct {
		digits    string
		saturates bool
		dtype     dtype
		first     Value
	}{
		{"-9223372036854775808", false, dtNullable, IntValue(math.MinInt64)},
		{"-9223372036854775808", true, dtNullable, IntValue(math.MinInt64)},
		{"-9223372036854775809", false, dtNullable, IntValue(math.MinInt64)},
		{"-9223372036854780808", true, dtFloat64, FloatValue(-9223372036854780808)},
		{"9223372036854775808", true, dtNullable, IntValue(math.MaxInt64)},
		{"9223372036854775808", false, dtFloat64, FloatValue(9223372036854775808)},
		{"9223372036854777808", true, dtFloat64, FloatValue(9223372036854777808)},
	}
	for _, test := range cases {
		float64ToInt64Saturates = test.saturates
		got, err := excelUtilsCoerce(column(test.digits))
		if err != nil {
			t.Fatalf("%s (saturates %v): %v", test.digits, test.saturates, err)
		}
		if got.dtype != test.dtype || got.values[0] != test.first {
			t.Fatalf("%s (saturates %v) = %v %+v, want %v %+v", test.digits, test.saturates, got.dtype, got.values[0], test.dtype, test.first)
		}
	}
}
