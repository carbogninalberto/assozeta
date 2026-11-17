package dto

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// Expected values are Python 3.13's decimal and datetime.strptime results.
func TestInstructorDecimalMatchesPython(t *testing.T) {
	cases := []struct{ amount, percentage, text, quotient, product string }{
		{"10.5", "50.00", "10.5", "0.50", "5.250"},
		{"1e-7", "50.00", "1E-7", "0.50", "5.0E-8"},
		{"21", "50.00", "21", "0.50", "10.50"},
		{" 1_000 ", "33.33", "1000", "0.3333", "333.3000"},
		{"-0", "12.50", "-0", "0.125", "-0.000"},
		{"0.000", "10.00", "0.000", "0.10", "0.00000"},
		{"123456789012345678", "99.99", "123456789012345678", "0.9999", "123444443333444443.4322"},
		{"1E+5", "1.00", "1E+5", "0.01", "1E+3"},
		{".5", "25.00", "0.5", "0.25", "0.125"},
		{"5.", "-5.00", "5", "-0.05", "-0.25"},
		// More than 28 digits: rounded half even.
		{"12345678901234567890123456789", "50.00", "12345678901234567890123456789", "0.50", "6172839450617283945061728394"},
		{"12345678901234567890123456788", "50.00", "12345678901234567890123456788", "0.50", "6172839450617283945061728394"},
		{"12345678901234567890123456791", "50.00", "12345678901234567890123456791", "0.50", "6172839450617283945061728396"},
		{"12345678901234567890123456789", "99.99", "12345678901234567890123456789", "0.9999", "1.234444433334444443333444444E+28"},
		{"99999999999999999999999999999", "50.00", "99999999999999999999999999999", "0.50", "5.000000000000000000000000000E+28"},
		{"Infinity", "50.00", "Infinity", "0.50", "Infinity"},
		{"-inf", "50.00", "-Infinity", "0.50", "-Infinity"},
		{"-NaN007", "50.00", "-NaN7", "0.50", "-NaN7"},
	}
	for _, c := range cases {
		amount, outcome := ParseInstructorDecimal(c.amount)
		percentage, percentageOutcome := ParseInstructorDecimal(c.percentage)
		if outcome != DecimalFinite || percentageOutcome != DecimalFinite {
			t.Fatalf("%q/%q: outcome %v %v", c.amount, c.percentage, outcome, percentageOutcome)
		}
		quotient := percentage.PercentOf()
		product, result := amount.Times(quotient)
		if result != TimesOK || amount.String() != c.text || quotient.String() != c.quotient || product.String() != c.product {
			t.Fatalf("%q*%q/100: %s %s %s", c.amount, c.percentage, amount, quotient, product)
		}
	}
	for text, want := range map[string]DecimalOutcome{"10,5": DecimalInvalid, "True": DecimalInvalid, "None": DecimalInvalid, "NaN": DecimalFinite,
		"-Infinity": DecimalFinite, "NaN" + "1234567890123456789012345678": DecimalFinite,
		"1E1000000000000000000": DecimalInvalid, "1E-1999999999999999998": DecimalInvalid} {
		if _, outcome := ParseInstructorDecimal(text); outcome != want {
			t.Fatalf("%q: %v, want %v", text, outcome, want)
		}
	}
}

func TestCompensationPaymentDateMatchesStrptime(t *testing.T) {
	cases := map[string]string{
		"2026-09-30T10:00:00.25+02:00":  "2026-09-30T10:00:00.250000",
		"2026-9-3T1:2:3.1":              "2026-09-03T01:02:03.100000",
		"2026-09-30t10:00:00.000001":    "2026-09-30T10:00:00.000001",
		"2026-02-30T10:00:00.0":         "",
		"2026-09-30T10:00:00":           "",
		"2026-09-30T10:00:60.0":         "",
		"2026-09-30T24:00:00.0":         "",
		"2026-09-30T10:00:00.000Z":      "",
		"2026-09-30T10:00:00.1234567+1": "",
	}
	for input, want := range cases {
		moment, ok := CompensationPaymentDate(input)
		got := ""
		if ok {
			got = moment.Format("2006-01-02T15:04:05.000000")
		}
		if got != want {
			t.Fatalf("%q: %q, want %q", input, got, want)
		}
	}
	if _, ok := CompensationPaymentDate(123); ok {
		t.Fatal("a non-string payment_date parses")
	}
}

// instructorDecimalGolden is testdata/instructor_decimal.json, generated from
// CPython 3.13's decimal by testdata/gen_instructor_decimal.py.
type instructorDecimalGolden struct {
	Percentages []struct {
		Text     string `json:"text"`
		Quotient string `json:"quotient"`
	} `json:"percentages"`
	Parse []struct {
		Text    string  `json:"text"`
		Outcome string  `json:"outcome"`
		Str     *string `json:"str"`
	} `json:"parse"`
	Multiply []struct {
		Amount     string  `json:"amount"`
		Percentage string  `json:"percentage"`
		Outcome    string  `json:"outcome"`
		Str        *string `json:"str"`
	} `json:"multiply"`
}

func loadInstructorDecimalGolden(t *testing.T) instructorDecimalGolden {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("testdata", "instructor_decimal.json"))
	if err != nil {
		t.Fatal(err)
	}
	var golden instructorDecimalGolden
	if err := json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	if len(golden.Parse) < 2000 || len(golden.Multiply) < 2000 {
		t.Fatalf("corpus too small: %d parse, %d multiply", len(golden.Parse), len(golden.Multiply))
	}
	return golden
}

// TestInstructorDecimalGolden is CPython's Decimal(text), str(Decimal(p) / 100)
// and str(Decimal(a) * (Decimal(p) / 100)) for the compensation notes.
func TestInstructorDecimalGolden(t *testing.T) {
	golden := loadInstructorDecimalGolden(t)
	for _, c := range golden.Percentages {
		percentage, outcome := ParseInstructorDecimal(c.Text)
		if outcome != DecimalFinite || percentage.Special != "" {
			t.Fatalf("percentage %q: outcome %v", c.Text, outcome)
		}
		if got := percentage.PercentOf().String(); got != c.Quotient {
			t.Fatalf("%q / 100: %s, want %s", c.Text, got, c.Quotient)
		}
	}
	for _, c := range golden.Parse {
		value, outcome := ParseInstructorDecimal(c.Text)
		if c.Outcome == "invalid" {
			if outcome != DecimalInvalid {
				t.Fatalf("Decimal(%q): %v, want InvalidOperation", c.Text, outcome)
			}
			continue
		}
		if outcome != DecimalFinite {
			t.Fatalf("Decimal(%q): %v, want a value", c.Text, outcome)
		}
		if c.Str != nil && value.String() != *c.Str {
			t.Fatalf("str(Decimal(%q)): %s, want %s", c.Text, value.String(), *c.Str)
		}
	}
	for _, c := range golden.Multiply {
		amount, outcome := ParseInstructorDecimal(c.Amount)
		if outcome != DecimalFinite {
			t.Fatalf("Decimal(%q): %v", c.Amount, outcome)
		}
		percentage, percentageOutcome := ParseInstructorDecimal(c.Percentage)
		if percentageOutcome != DecimalFinite || percentage.Special != "" {
			t.Fatalf("percentage %q: outcome %v", c.Percentage, percentageOutcome)
		}
		product, result := amount.Times(percentage.PercentOf())
		switch c.Outcome {
		case "ok":
			if result != TimesOK {
				t.Fatalf("%q * %q/100: result %v, want TimesOK", c.Amount, c.Percentage, result)
			}
			if c.Str != nil && product.String() != *c.Str {
				t.Fatalf("%q * %q/100: %s, want %s", c.Amount, c.Percentage, product.String(), *c.Str)
			}
		case "invalid":
			if result != TimesInvalid {
				t.Fatalf("%q * %q/100: result %v, want TimesInvalid", c.Amount, c.Percentage, result)
			}
		case "overflow":
			if result != TimesOverflow {
				t.Fatalf("%q * %q/100: result %v, want TimesOverflow", c.Amount, c.Percentage, result)
			}
		default:
			t.Fatalf("%q * %q/100: unknown outcome %q", c.Amount, c.Percentage, c.Outcome)
		}
	}
}
