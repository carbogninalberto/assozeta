package pydateutil

import (
	"testing"
	"time"
)

// fieldNames is the set of fields dateutil's parser._parse filled in, in the
// order pandas walks them.
func fieldNames(fields Fields) (names string) {
	for index, set := range []bool{fields.Year != nil, fields.Month != nil, fields.Day != nil, fields.Hour != nil,
		fields.Minute != nil, fields.Second != nil, fields.HasMicrosecond} {
		if set {
			names += "ymdhmsu"[index : index+1]
		}
	}
	return names
}

// TestParseFields is pandas' dateutil_parse without the replace: the set of
// fields dateutil's parser._parse wrote (and no others), and pandas'
// datetime(1, 1, 1).replace(**them).
// Expected values: Python 3.13.16 with python-dateutil 2.9.0.post0:
//
//	>>> from dateutil.parser import DEFAULTPARSER
//	>>> res, _ = DEFAULTPARSER._parse('30th')
//	>>> [f for f in ('year', 'month', 'day', 'hour', 'minute', 'second',
//	...              'microsecond') if getattr(res, f) is not None]
//	['day']
//	>>> datetime(1, 1, 1).replace(**{f: getattr(res, f) for f in (...) if getattr(res, f) is not None})
//	datetime.datetime(1, 1, 30, 0, 0)
//	>>> # '2023' -> ['year'], 2023-01-01; 'Jan' -> ['month'], 0001-01-01; '10:30' -> ['hour', 'minute'],
//	>>> # 0001-01-01 10:30; '03/02/1990' -> ['year', 'month', 'day'], 1990-03-02;
//	>>> # '2023-03-15 10:30:00.1234567' -> all seven, .123456 (dateutil keeps six digits);
//	>>> # '1.5' -> ['day'], 0001-01-01 (dateutil reads it as the first of January 00:00)
//	>>> # 'Feb 29', '30 Feb', '45000' -> ValueError from datetime.replace
//	>>> # '29 febbraio', 'x' -> parser._parse answers None
func TestParseFields(t *testing.T) {
	for _, tc := range []struct {
		text  string
		names string // the fields dateutil set; "" for a text it does not read
		at    string // datetime(1, 1, 1).replace(**the fields), "" for an error
	}{
		{"30th", "d", "0001-01-30 00:00:00"},
		{"Jan 31", "md", "0001-01-31 00:00:00"},
		{"2023", "y", "2023-01-01 00:00:00"},
		{"Jan", "m", "0001-01-01 00:00:00"},
		{"10:30", "hm", "0001-01-01 10:30:00"},
		{"12:00 AM", "hm", "0001-01-01 00:00:00"},
		{"March 2023", "ym", "2023-03-01 00:00:00"},
		{"03/02/1990", "ymd", "1990-03-02 00:00:00"},
		{"2023-03-15 10:30:00.123", "ymdhmsu", "2023-03-15 10:30:00.123"},
		{"2023-03-15 10:30:00.1234567", "ymdhmsu", "2023-03-15 10:30:00.123456"},
		{"1.5", "d", "0001-01-01 00:00:00"},
		{"Feb 29", "md", ""},
		{"30 Feb", "md", ""},
		{"45000", "y", ""},
		{"29 febbraio", "", ""},
		{"x", "", ""},
	} {
		fields, err := ParseFields(tc.text, 2026, time.UTC)
		if tc.names == "" {
			if _, ok := err.(*Error); !ok {
				t.Errorf("%q: %v, want a ParserError", tc.text, err)
			}
			continue
		}
		if tc.at == "" {
			if _, ok := err.(*Error); !ok || !err.(*Error).IsValueError() {
				t.Errorf("%q: %v, want a ValueError (the date replace raises)", tc.text, err)
			}
			continue
		}
		if err != nil {
			t.Errorf("%q: %v", tc.text, err)
			continue
		}
		if got := fieldNames(fields); got != tc.names {
			t.Errorf("%q: fields %q, want %q", tc.text, got, tc.names)
			continue
		}
		want, parseErr := time.Parse("2006-01-02 15:04:05.999999999", tc.at)
		if parseErr != nil {
			t.Fatal(parseErr)
		}
		if fields.Result.Year != want.Year() || fields.Result.Month != int(want.Month()) || fields.Result.Day != want.Day() ||
			fields.Result.Hour != want.Hour() || fields.Result.Minute != want.Minute() || fields.Result.Second != want.Second() ||
			fields.Result.Microsecond != want.Nanosecond()/1000 || fields.Result.HasTZ {
			t.Errorf("%q: %+v, want %s", tc.text, fields.Result, tc.at)
		}
	}
}
