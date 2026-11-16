package dto

import (
	"fmt"
	"testing"
)

// strftime('%Y') is the platform C library's: glibc (the Linux images) leaves a
// year below 1000 unpadded, the BSD library on macOS pads it to four digits.
func TestPythonStrftimeYear(t *testing.T) {
	saved := strftimeGOOS
	defer func() { strftimeGOOS = saved }()
	for _, tc := range []struct {
		platform string
		year     int
		want     string
	}{
		{"linux", 2026, "2026"},
		{"linux", 1000, "1000"},
		{"linux", 999, "999"},
		{"linux", 1, "1"},
		{"linux", 0, "0"},
		{"linux", -1, "-1"},
		{"darwin", 2026, "2026"},
		{"darwin", 1000, "1000"},
		{"darwin", 999, "0999"},
		{"darwin", 1, "0001"},
		{"darwin", 0, "0000"},
		{"darwin", -1, "-001"},
		{"freebsd", 999, "999"},
	} {
		strftimeGOOS = tc.platform
		if got := PythonStrftimeYear(tc.year); got != tc.want {
			t.Errorf("%s year %d: %q, want %q", tc.platform, tc.year, got, tc.want)
		}
	}
}

// The statistic/report view re-reads strftime('%Y-%m-%d') with strptime and
// invoice_customers_add's DateField re-reads strftime('%d/%m/%Y') the same way;
// CPython's strptime needs exactly four year digits, so a date before 1000
// survives the round trip only where strftime pads the year.
func TestPythonStrftimeYearRoundTrip(t *testing.T) {
	saved := strftimeGOOS
	defer func() { strftimeGOOS = saved }()
	for _, tc := range []struct {
		platform string
		ymd, dmy string
		parses   bool
	}{
		{"linux", "999-03-05", "05/03/999", false},
		{"darwin", "0999-03-05", "05/03/0999", true},
	} {
		strftimeGOOS = tc.platform
		ymd := fmt.Sprintf("%s-%02d-%02d", PythonStrftimeYear(999), 3, 5)
		dmy := fmt.Sprintf("%02d/%02d/%s", 5, 3, PythonStrftimeYear(999))
		if ymd != tc.ymd || dmy != tc.dmy {
			t.Fatalf("%s: %q / %q, want %q / %q", tc.platform, ymd, dmy, tc.ymd, tc.dmy)
		}
		if _, ok := PythonStrptimeDate(ymd); ok != tc.parses {
			t.Errorf("%s: strptime(%q) parsed %v, want %v", tc.platform, ymd, ok, tc.parses)
		}
		if _, ok := PythonStrptimeDMY(dmy); ok != tc.parses {
			t.Errorf("%s: strptime(%q) parsed %v, want %v", tc.platform, dmy, ok, tc.parses)
		}
	}
}
