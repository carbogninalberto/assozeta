package fatturapa

import (
	"errors"
	"math"
	"math/big"
	"strings"
	"testing"
)

// The expected values of every table below were computed with the reference
// interpreter on a38 0.1.8's own classes; the comment above each table names
// the Python expression it evaluates.

func pyIntOf(text string) pyInt {
	value, ok := new(big.Int).SetString(text, 10)
	if !ok {
		panic("not an integer: " + text)
	}
	return pyInt{value}
}

// shortText trims a long JSON literal for an error message.
func shortText(text string) string {
	if len(text) <= 24 {
		return text
	}
	return text[:24] + "..."
}

// json.loads('1e400') is inf, and jsonb prints such a numeric as text with a
// point or an exponent, so json.loads always makes a float of it.
func TestParseJSONOverflowNumber(t *testing.T) {
	for _, tc := range []struct {
		text string
		want float64
	}{
		{`[1e400]`, math.Inf(1)},
		{`[-1e400]`, math.Inf(-1)},
		{`[` + strings.Repeat("9", 400) + `.5]`, math.Inf(1)},
		{`[-` + strings.Repeat("9", 400) + `.5]`, math.Inf(-1)},
		{`[1e999999999999999999999]`, math.Inf(1)},
		{`[1.5e308]`, 1.5e308},
	} {
		value, err := parseJSON(tc.text)
		if err != nil {
			t.Fatalf("%s: %v", shortText(tc.text), err)
		}
		number, ok := value.([]any)[0].(pyFloat)
		if !ok || float64(number) != tc.want {
			t.Errorf("%s: got %v, want %v", shortText(tc.text), value, tc.want)
		}
	}
}

// str(float('inf')) is 'inf' and str(float('-inf')) is '-inf'; both are truthy,
// and Decimal.from_float of either is Decimal('Infinity'), whose digit tuple
// has no exponent for DecimalField.clean_value to compare (fields.py:341-351).
func TestOverflowNumberConsumers(t *testing.T) {
	for _, tc := range []struct {
		value float64
		text  string
	}{
		{math.Inf(1), "inf"},
		{math.Inf(-1), "-inf"},
	} {
		number := pyFloat(tc.value)
		if !pyTruthy(number) {
			t.Errorf("%v: not truthy", tc.value)
		}
		text, err := pyStr(number)
		if err != nil || text == nil || *text != tc.text {
			t.Errorf("%v: str() = %v, %v; want %q", tc.value, text, err, tc.text)
		}
		if _, err := (&builder{}).decimalField(number, 2, 8); !errors.Is(err, ErrInvalid) {
			t.Errorf("%v: Decimal() gave %v, want ErrInvalid", tc.value, err)
		}
	}
}

// str() of a list or dict is its repr(); dict keys keep the JSON order and a
// repeated key keeps its first position with the last value.
func TestPyStrReprOfContainers(t *testing.T) {
	for _, tc := range []struct{ text, want string }{
		{`["a", 1, 1.0, -0.0, 1e400, -1e400, 2.5, 100000000000000000000, true, false, null]`,
			`['a', 1, 1.0, -0.0, inf, -inf, 2.5, 100000000000000000000, True, False, None]`},
		{`{"b": 1, "a": 2, "b": 3}`, `{'b': 3, 'a': 2}`},
		{`[[1, 2], {"x": [null]}]`, `[[1, 2], {'x': [None]}]`},
		{`{"": ""}`, `{'': ''}`},
		{`[]`, `[]`},
		{`{}`, `{}`},
		{`["\u00e8", "\u0001", "\u007f", "\u00a0"]`, "['\u00e8', '\\x01', '\\x7f', '\\xa0']"},
		// str.__repr__ picks the double quote when the text has a single one.
		{`{"k": "quo'te", "d": "double\"q", "n": "line\nbreak", "e": "\ud83c\udf89"}`,
			`{'k': "quo'te", 'd': 'double"q', 'n': 'line\nbreak', 'e': '🎉'}`},
	} {
		value, err := parseJSON(tc.text)
		if err != nil {
			t.Fatalf("%s: %v", tc.text, err)
		}
		text, err := pyStr(value)
		if err != nil || text == nil || *text != tc.want {
			t.Errorf("str(%s) = %v, %v; want %q", tc.text, text, err, tc.want)
		}
	}
}

// Decimal([sign, digits, exponent]) — _decimal accepts a list like a tuple and
// a bool like an int; 'F', 'n' and 'N' build Infinity, NaN and sNaN, which the
// field rejects.
func TestPyDecimalTuple(t *testing.T) {
	digits := func(values ...any) []any { return values }
	for _, tc := range []struct {
		value []any
		want  string // "" for ErrInvalid
	}{
		{[]any{pyIntOf("0"), digits(pyIntOf("1")), pyIntOf("-2")}, "0.01"},
		{[]any{pyIntOf("1"), digits(pyIntOf("1"), pyIntOf("5")), pyIntOf("0")}, "-15"},
		{[]any{pyIntOf("-1"), digits(pyIntOf("1")), pyIntOf("0")}, ""},
		{[]any{pyIntOf("2"), digits(pyIntOf("1")), pyIntOf("0")}, ""},
		{[]any{pyIntOf("0"), digits(pyIntOf("10")), pyIntOf("0")}, ""},
		{[]any{true, digits(pyIntOf("5")), pyIntOf("0")}, "-5"},
		{[]any{false, digits(pyIntOf("5")), pyIntOf("0")}, "5"},
		{[]any{pyIntOf("0"), digits(true, pyIntOf("5")), pyIntOf("0")}, "15"},
		{[]any{pyIntOf("0"), digits(pyIntOf("11")), pyIntOf("0")}, ""},
		{[]any{pyIntOf("0"), digits(pyIntOf("5")), true}, "5E+1"},
		{[]any{pyIntOf("0"), digits(pyIntOf("5")), false}, "5"},
		{[]any{pyIntOf("0"), digits(), pyIntOf("0")}, "0"},
		{[]any{pyIntOf("0"), digits(pyIntOf("0"), pyIntOf("0"), pyIntOf("1")), pyIntOf("0")}, "1"},
		{[]any{pyIntOf("0"), digits(pyIntOf("1")), "F"}, ""},
		{[]any{pyIntOf("1"), digits(pyIntOf("1")), "n"}, ""},
		{[]any{pyIntOf("0"), digits(pyIntOf("1")), "N"}, ""},
		{[]any{pyIntOf("0"), digits(pyIntOf("1")), "x"}, ""},
		{[]any{pyIntOf("0"), digits(pyIntOf("1")), pyFloat(1)}, ""},
		{[]any{pyIntOf("0"), digits(pyIntOf("1")), nil}, ""},
		{[]any{pyIntOf("0"), digits(pyIntOf("1")), pyIntOf("0"), pyIntOf("0")}, ""},
		// libmpdec's exponent range bounds the adjusted exponent.
		{[]any{pyIntOf("0"), digits(pyIntOf("1"), pyIntOf("1")), pyIntOf("999999999999999998")},
			"1.1E+999999999999999999"},
		{[]any{pyIntOf("0"), digits(pyIntOf("1"), pyIntOf("1")), pyIntOf("999999999999999999")}, ""},
		{[]any{pyIntOf("0"), digits(pyIntOf("1"), pyIntOf("1")), pyIntOf("-1999999999999999997")},
			"1.1E-1999999999999999996"},
		{[]any{pyIntOf("0"), digits(pyIntOf("1")), pyIntOf("-999999999")}, "1E-999999999"},
		{[]any{pyIntOf("0"), digits(pyIntOf("1")), pyIntOf("-9")}, "1E-9"},
		{[]any{pyIntOf("0"), digits("1"), pyIntOf("0")}, ""},
	} {
		value, err := pyDecimal(tc.value)
		if tc.want == "" {
			if !errors.Is(err, ErrInvalid) {
				t.Errorf("Decimal(%v) = %v, %v; want ErrInvalid", tc.value, value, err)
			}
			continue
		}
		if err != nil || value == nil || value.String() != tc.want {
			t.Errorf("Decimal(%v) = %v, %v; want %q", tc.value, value, err, tc.want)
		}
	}
}

// Decimal(str) strips str whitespace, ignores '_', reads every Unicode decimal
// digit and bounds the adjusted exponent by libmpdec's range; the special
// spellings reach the field's range step, which rejects them (ErrInvalid here).
func TestParseDecimalText(t *testing.T) {
	for _, tc := range []struct{ text, want string }{
		{"1__2", "12"},
		{"_1", "1"},
		{"1_", "1"},
		{"1_.5", "1.5"},
		{"1.5_0", "1.50"},
		{"1e1_0", "1E+10"},
		{"1_2.3_4", "12.34"},
		{"1._5", "1.5"},
		{".5_", "0.5"},
		{"1_e2", "1E+2"},
		{"1e2_", "1E+2"},
		{" 5 ", "5"},
		{"\u00a05\u3000", "5"},
		{"\u2003-5\u2003", "-5"},
		{"\t5\n", "5"},
		{"\u0661\u0662", "12"},
		{"\uff11\uff12", "12"},
		{"\u0661\u0662.\u0663", "12.3"},
		{"-\u0661\u0662", "-12"},
		{"1e\u0661\u0662", "1E+12"},
		{"+5", "5"},
		{".5", "0.5"},
		{"5.", "5"},
		{"1e999999999", "1E+999999999"},
		{"1e-999999999", "1E-999999999"},
		{"1e999999999999999999", "1E+999999999999999999"},
		{"0.1e1000000000000000000", "1E+999999999999999999"},
		{"0001e999999999999999999", "1E+999999999999999999"},
		{"1e-1999999999999999997", "1E-1999999999999999997"},
		{"1 2", ""},
		{"", ""},
		{" ", ""},
		{"1.2.3", ""},
		{"Infinity", ""},
		{"-inf", ""},
		{"NaN", ""},
		{"sNaN", ""},
		{"in_f", ""},
		{"1e1000000000000000000", ""},
		{"1e-1999999999999999998", ""},
		{"1e9223372036854775807", ""},
	} {
		value, ok := parseDecimal(tc.text)
		if tc.want == "" {
			if ok {
				t.Errorf("Decimal(%q) = %v, want a raise", tc.text, value)
			}
			continue
		}
		if !ok || value.String() != tc.want {
			t.Errorf("Decimal(%q) = %v, %v; want %q", tc.text, value, ok, tc.want)
		}
	}
}

// DecimalField.clean_value (fields.py:331-353) of an exponent of any size: the
// coefficient length and the exponent decide the result, never a huge power of
// ten. Python quantizes 1E-999999999 to 0E-8 with decimals (2, 8) and to 0.00
// with (2, 2), keeps the sign, and raises InvalidOperation for 1E+999999999.
func TestDecimalFieldExtremes(t *testing.T) {
	b := &builder{}
	for _, tc := range []struct {
		value   any
		minimum int
		maximum int
		want    string
	}{
		{"1e-999999999", 2, 8, "0E-8"},
		{"-1e-999999999", 2, 8, "-0E-8"},
		{"1e-1999999999999999997", 2, 8, "0E-8"},
		{"0.000000001", 2, 8, "0E-8"},
		{[]any{pyIntOf("0"), []any{pyIntOf("1")}, pyIntOf("-999999999")}, 2, 8, "0E-8"},
		{"1e-999999999", 2, 2, "0.00"},
		{"-1e-999999999", 2, 2, "-0.00"},
		{"-0.001", 2, 2, "-0.00"},
		{"0.005", 2, 2, "0.01"},
		{"1e18", 2, 8, "1000000000000000000.00"},
		{"1e999999999", 2, 8, ""},
		{"-1e999999999", 2, 8, ""},
		{"1e999999999999999999", 2, 8, ""},
		{"1e26", 2, 8, ""},
		{"1234567890123456789012345678.9", 2, 8, ""},
		{"123456789012345678901234567.89", 2, 8, "123456789012345678901234567.89"},
		{"1.2345678901234567890123456789", 2, 8, "1.23456789"},
	} {
		value, err := b.decimalField(tc.value, tc.minimum, tc.maximum)
		if tc.want == "" {
			if !errors.Is(err, ErrInvalid) {
				t.Errorf("clean_value(%v, %d, %d) = %v, %v; want ErrInvalid",
					tc.value, tc.minimum, tc.maximum, value, err)
			}
			continue
		}
		if err != nil || value == nil || value.String() != tc.want {
			t.Errorf("clean_value(%v, %d, %d) = %v, %v; want %q",
				tc.value, tc.minimum, tc.maximum, value, err, tc.want)
		}
	}
}
