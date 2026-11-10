package dto

import "testing"

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
		"-Infinity": DecimalFinite, "NaN" + "1234567890123456789012345678": DecimalNotPorted} {
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
