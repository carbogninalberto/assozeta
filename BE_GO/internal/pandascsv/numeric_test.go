package pandascsv

import (
	"math"
	"runtime"
	"slices"
	"testing"
)

// TestParseFloatExponent pins the C int the exponent is accumulated in: it
// wraps like two's complement, in its digits and in its sum. The expectations
// are pandas 2.2.3:
//
//	pandas.read_csv(io.StringIO('a\n' + text + '\n'), sep=';')['a']
func TestParseFloatExponent(t *testing.T) {
	for _, tc := range []struct {
		text    string
		invalid bool
		bits    uint64
	}{
		// The C int's digits wrap: 2147483648 is -2147483648, so the
		// exponent is -2147483648 and the value 0.0.
		{text: "1e2147483648"},
		{text: "2e-2147483648"},
		{text: "1e-2147483648"},
		{text: "1e-2147483647"},
		{text: "1e-2147483646"},
		// 4294967296 is 0, so the value is the mantissa.
		{"1e4294967296", false, 0x3ff0000000000000},
		{"5e4294967296", false, 0x4014000000000000},
		{"1e4294967297", false, 0x4024000000000000},
		// 21474836470 is 4294967286, that is -10: 1e-10.
		{"1e21474836470", false, 0x3ddb7cdfd9d7bdbb},
		// The sum wraps too: 1e-4294967295 is 4294967295, that is -1, and
		// subtracting it gives 1.
		{"1e-4294967295", false, 0x4024000000000000},
		{"1e-4294967296", false, 0x3ff0000000000000},
		// -2147483649 is 2147483647, so the exponent overflows: not a float,
		// like the positive exponents above 308.
		{text: "1e-2147483649", invalid: true},
		{text: "1e2147483647", invalid: true},
		{text: "1e2147483646", invalid: true},
		{text: "1e9999999999", invalid: true},
		{text: "12345e2147483644", invalid: true},
		{text: "0.1e2147483647", invalid: true},
		{text: "0.1e2147483648", invalid: true},
		// 17 exponent digits at most, so the rest is left: not a float.
		{text: "1e99999999999999999", invalid: true},
		{text: "1e000000000000000005", invalid: true},
		{text: "1e0000000000000000001", invalid: true},
		// 1e308 is the largest exponent the scaling holds.
		{"1e308", false, 0x7fe1ccf385ebc8a0},
		{text: "1e309", invalid: true},
	} {
		value, status := parseFloat([]byte(tc.text))
		if tc.invalid {
			if status != floatInvalid {
				t.Errorf("%q: %v %v, want not a float", tc.text, value, status)
			}
			continue
		}
		if status != floatOK || math.Float64bits(value) != tc.bits {
			t.Errorf("%q: %v (%#x) %v, want %#x", tc.text, value, math.Float64bits(value), status, tc.bits)
		}
	}
}

// TestParseFloatPlatform pins the digit accumulation to the pandas wheel of
// the platform: measured with pandas 2.2.3, '3.14159265358979323846' is
// 0x400921fb54442d18 where the compiler contracts 'number * 10 + digit' into a
// fused multiply-add (macOS arm64) and 0x400921fb54442d17 where it does not
// (Linux x86_64 and aarch64). See floatFMA.
func TestParseFloatPlatform(t *testing.T) {
	const text = "3.14159265358979323846"
	fused, plain := uint64(0x400921fb54442d18), uint64(0x400921fb54442d17)
	contracted, measured := floatFMA[runtime.GOOS+"/"+runtime.GOARCH]
	value, status := parseFloat([]byte(text))
	switch {
	case !measured:
		if status != floatPlatform {
			t.Fatalf("%q: %v %v, want floatPlatform", text, value, status)
		}
	case status != floatOK:
		t.Fatalf("%q: %v %v", text, value, status)
	case contracted && math.Float64bits(value) != fused:
		t.Errorf("%q: %#x, want the fused %#x", text, math.Float64bits(value), fused)
	case !contracted && math.Float64bits(value) != plain:
		t.Errorf("%q: %#x, want the plain %#x", text, math.Float64bits(value), plain)
	}
}

// TestReadFloatTexts is the same conversion through Read: a float text is a
// float64 column, and one pandas does not read (the exponent wraps out of
// range, or 17 exponent digits are not all read) leaves the column object.
func TestReadFloatTexts(t *testing.T) {
	for _, tc := range []struct {
		text  string
		float bool
		text2 string // str(value) of a float64 column
	}{
		{"1e2147483648", true, "0.0"},
		{"1e4294967296", true, "1.0"},
		{"1e-2147483649", false, ""},
		{"1e99999999999999999", false, ""},
		{"0.1e2147483647", false, ""},
		{"1e309", false, ""},
	} {
		frame, err := Read([]byte("a\n" + tc.text + "\n"))
		if err != nil {
			t.Fatalf("%q: %v", tc.text, err)
		}
		want := Object
		if tc.float {
			want = Float64
		}
		if !slices.Equal(frame.Dtypes, []Dtype{want}) {
			t.Errorf("%q: dtypes %v, want %v", tc.text, frame.Dtypes, want)
			continue
		}
		if tc.float && (len(frame.Rows) != 1 || frame.Rows[0][0].Text() != tc.text2) {
			t.Errorf("%q: rows %v, want %v", tc.text, frame.Rows, tc.text2)
		}
		if !tc.float && (len(frame.Rows) != 1 || frame.Rows[0][0].S != tc.text) {
			t.Errorf("%q: rows %v, want the text", tc.text, frame.Rows)
		}
	}
}
