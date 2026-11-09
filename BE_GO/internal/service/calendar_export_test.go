package service

import (
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// Vectors from django.utils.dateparse.parse_datetime (Django 5.2.18, Python 3.13).
func TestCalendarExportParse(t *testing.T) {
	for _, tc := range []struct {
		input, want string
		fails       error
	}{
		{input: "2026-03-10", want: "20260310T000000"},
		{input: "20260310", want: "20260310T000000"},
		{input: "2026-03-10T09", want: "20260310T090000"},
		{input: "2026-03-10T0905", want: "20260310T090500"},
		{input: "2026-03-10T090507.5", want: "20260310T090507"},
		{input: "2026-03-10 9:05", want: "20260310T090500"},
		{input: "2026-3-5T9:5:7,1234567", want: "20260305T090507"},
		{input: "2026-03-10T09:05:07.1234567Z", want: "20260310T090507Z"},
		{input: "2026-03-10T09:05-00:00", want: "20260310T090500Z"},
		{input: "2026-03-10T09:05:07 +0000", want: "20260310T090507Z"},
		{input: "2026-03-10T10:00\n", want: "20260310T100000"},
		{input: "2026-02-30"}, // None
		{input: "domani"},     // None
		{input: "2026-02-30T10:00", fails: domain.ErrMiscRoutesServer}, // ValueError
		{input: "2026-03-10T25:00", fails: domain.ErrMiscRoutesServer},
		{input: "0000-01-01T00:00", fails: domain.ErrMiscRoutesServer},
		{input: "2026-03-10T10:00+01:00", fails: domain.ErrMiscRoutesNotPorted}, // icalendar TZID
		{input: "2026-03-10x10:00", fails: domain.ErrMiscRoutesNotPorted},       // any separator
		{input: "2026-03-10T10:00 à", fails: domain.ErrMiscRoutesNotPorted},     // non-ASCII
	} {
		moment, err := calendarExportParse(tc.input)
		if tc.fails != nil {
			if !errors.Is(err, tc.fails) {
				t.Errorf("%q: error %v, want %v", tc.input, err, tc.fails)
			}
			continue
		}
		if err != nil {
			t.Errorf("%q: unexpected error %v", tc.input, err)
			continue
		}
		got := ""
		if moment != nil {
			got = moment.render()
		}
		if got != tc.want {
			t.Errorf("%q: got %q, want %q", tc.input, got, tc.want)
		}
	}
}

// icalendar's foldline and escape_char.
func TestCalendarExportFoldEscape(t *testing.T) {
	if got := calendarExportEscape("a\\Nb;c,d\\e\r\nf\ng\rh"); got != `a\nb\;c\,d\\e\nf\ng`+"\r"+`h` {
		t.Errorf("escape: %q", got)
	}
	line := "SUMMARY:" + string(make([]byte, 0))
	for i := 0; i < 80; i++ {
		line += "x"
	}
	folded := calendarExportFold(line)
	if folded[:74] != line[:74] || folded[74:77] != "\r\n " || folded[77:] != line[74:] {
		t.Errorf("ascii fold: %q", folded)
	}
	// 'à' is two octets: the fold comes before the character reaching 75.
	wide := "SUMMARY:"
	for i := 0; i < 40; i++ {
		wide += "à"
	}
	folded = calendarExportFold(wide)
	if folded[:8+33*2] != wide[:8+33*2] || folded[8+33*2:8+33*2+3] != "\r\n " {
		t.Errorf("utf-8 fold: %q", folded)
	}
}
