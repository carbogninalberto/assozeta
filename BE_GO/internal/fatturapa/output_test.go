package fatturapa

import (
	"strings"
	"testing"
	"time"
)

// strftime('%Y-%m-%d') writes the year as the C library does: glibc, which the
// Linux images use, leaves a year below 1000 unpadded ('999', '1'), while the
// macOS library pads it ('0999', '0001') — CPython 3.13's strftime calls the
// library, as service's pythonStrftimeYear documents. Django and Go run on the
// same platform. DateField.to_str is the only place the XML takes a date from
// strftime; the HTML and JSON print str(date), whose isoformat pads every year.
func TestXMLDatesBeforeYear1000(t *testing.T) {
	saved := goos
	defer func() { goos = saved }()
	for _, tc := range []struct{ goos, date, expiry, xmlDate, xmlExpiry string }{
		{"linux", "0999-01-02", "0001-12-01", "999-01-02", "1-12-01"},
		{"darwin", "0999-01-02", "0001-12-01", "0999-01-02", "0001-12-01"},
	} {
		goos = tc.goos
		invoice := goldenInvoiceNamed(t, "base")
		date, expiry := parseTestDate(t, tc.date), parseTestDate(t, tc.expiry)
		invoice.TransmittingDate, invoice.PaymentExpiryDate = &date, expiry

		xml, err := XML(invoice, "A")
		if err != nil {
			t.Fatalf("%s: %v", tc.goos, err)
		}
		if want := "<Data>" + tc.xmlDate + "</Data>"; !strings.Contains(xml, want) {
			t.Errorf("%s: XML lacks %q", tc.goos, want)
		}
		if want := "<DataScadenzaPagamento>" + tc.xmlExpiry + "</DataScadenzaPagamento>"; !strings.Contains(xml, want) {
			t.Errorf("%s: XML lacks %q", tc.goos, want)
		}

		html, err := HTML(invoice)
		if err != nil {
			t.Fatalf("%s: %v", tc.goos, err)
		}
		if want := "<strong>Data:</strong> " + tc.date; !strings.Contains(html, want) {
			t.Errorf("%s: HTML lacks %q", tc.goos, want)
		}
		if want := "<strong>Scadenza:</strong> " + tc.expiry; !strings.Contains(html, want) {
			t.Errorf("%s: HTML lacks %q", tc.goos, want)
		}

		document, err := JSON(invoice, "A")
		if err != nil {
			t.Fatalf("%s: %v", tc.goos, err)
		}
		if want := `"data": "` + tc.date + `T00:00:00Z"`; !strings.Contains(document, want) {
			t.Errorf("%s: JSON lacks %q", tc.goos, want)
		}
		if want := `"data_scadenza_pagamento": "` + tc.expiry + `T00:00:00Z"`; !strings.Contains(document, want) {
			t.Errorf("%s: JSON lacks %q", tc.goos, want)
		}
	}
}

func parseTestDate(t *testing.T, text string) time.Time {
	t.Helper()
	parsed, err := time.Parse("2006-01-02", text)
	if err != nil {
		t.Fatal(err)
	}
	return parsed
}
