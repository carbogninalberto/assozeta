package emailrender

import (
	"math"
	"strings"
	"testing"
)

func TestLocalizeFloat(t *testing.T) {
	values := map[float64]string{
		1.5:                         "1.5",
		0.1:                         "0.1",
		math.Copysign(0, -1):        "-0.0",
		2.0:                         "2.0",
		1e16:                        "10000000000000000",
		1.5e16:                      "15000000000000000",
		1e-5:                        "0.00001",
		123456789.125:               "123456789.125",
		1e22:                        "10000000000000000000000",
		1e15:                        "1000000000000000.0",
		-1e16:                       "-10000000000000000",
		-2.5e-7:                     "-0.00000025",
		1e199:                       "1" + strings.Repeat("0", 199),
		1e200:                       "1e+200",
		math.MaxFloat64:             "1.7976931348623157e+308",
		math.SmallestNonzeroFloat64: "5e-324",
		math.Inf(1):                 "inf",
		math.Inf(-1):                "-inf",
	}
	half := 0.1
	values[half+0.2] = "0.30000000000000004"
	for value, expected := range values {
		got, err := localizeFloat(value, ".")
		if err != nil || got != expected {
			t.Fatalf("%v: %q != %q (%v)", value, got, expected, err)
		}
		localized, err := localizeFloat(value, ",")
		if want := strings.Replace(expected, ".", ",", 1); err != nil || localized != want {
			t.Fatalf("%v (it): %q != %q (%v)", value, localized, want, err)
		}
	}
}

func TestLocalizeDecimalBeyond200Digits(t *testing.T) {
	for text, expected := range map[string]string{
		"1E+200":                  "1e+200",
		"5E-324":                  "5e-324",
		"1.500E+250":              "1.500e+250",
		"1.7976931348623157E+308": "1.7976931348623157e+308",
		"1E+199":                  "1" + strings.Repeat("0", 199),
	} {
		got, err := localizeDecimal(text, ".")
		if err != nil || got != expected {
			t.Fatalf("%s: %q != %q (%v)", text, got, expected, err)
		}
		localized, err := localizeDecimal(text, ",")
		if want := strings.Replace(expected, ".", ",", 1); err != nil || localized != want {
			t.Fatalf("%s (it): %q != %q (%v)", text, localized, want, err)
		}
	}
}

func TestLocalizeNonFinite(t *testing.T) {
	for value, expected := range map[float64]string{
		math.Inf(1):  "inf",
		math.Inf(-1): "-inf",
		math.NaN():   "nan",
	} {
		for _, language := range []string{"it", "en"} {
			got, err := Localize(value, language)
			if err != nil || got != expected {
				t.Fatalf("%v %s: %q != %q (%v)", value, language, got, expected, err)
			}
		}
	}
	if got, err := Localize(math.Copysign(math.NaN(), -1), "it"); err != nil || got != "nan" {
		t.Fatalf("negative NaN: %q (%v)", got, err)
	}
}
