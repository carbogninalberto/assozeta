package pandasxlsx

import (
	"testing"
	"time"
)

// resolutionNames is the resolution as pandas names it (Resolution's string).
var resolutionNames = map[resolution]string{resoNano: "nanosecond", resoMicro: "microsecond", resoMilli: "millisecond",
	resoSecond: "second", resoMinute: "minute", resoHour: "hour", resoDay: "day", resoMonth: "month",
	resoQuarter: "quarter", resoYear: "year"}

const dateKeyLayout = "2006-01-02 15:04:05.000000000"

// TestParseDateKey is pandas' parse_datetime_string_with_reso: the datetime
// and the resolution of a DatetimeIndex key, with dateutil's raw fields and
// pandas' _DEFAULT_DATETIME (datetime(1, 1, 1)) as the default, not today.
// A text pandas raises on is no label.
//
// Expected values: Python 3.13.16 with pandas 2.2.3 (scratch/w3/probe_keys.py):
//
//	>>> import pandas._libs.tslibs.parsing as parsing
//	>>> parsing.parse_datetime_string_with_reso('30th')
//	(datetime.datetime(1, 1, 30, 0, 0), 'day')
//	>>> parsing.parse_datetime_string_with_reso('2023-03-15 10:30:00.1234567')
//	(Timestamp('2023-03-15 10:30:00.123456700'), 'nanosecond')
//	>>> parsing.parse_datetime_string_with_reso('10:30:00.123456789')  # not ISO-dated: dateutil keeps six digits
//	(datetime.datetime(1, 1, 1, 10, 30, 0, 123456), 'microsecond')
//	>>> parsing.parse_datetime_string_with_reso('Feb 29')
//	DateParseError: day is out of range for month: Feb 29
//	>>> parsing.parse_datetime_string_with_reso('31')   # _does_string_look_like_datetime: 31 < 1000
//	ValueError: Given date string "31" not likely a datetime
func TestParseDateKey(t *testing.T) {
	for _, tc := range []struct {
		text string
		at   string // "" for a text pandas raises on
		reso string
		tz   bool
	}{
		// Not likely a datetime, or unreadable: pandas raises, the key is no label.
		{"31", "", "", false},
		{"59", "", "", false},
		{"1.5", "", "", false},
		{"Feb 29", "", "", false},
		{"29 febbraio", "", "", false},
		{"30 Feb", "", "", false},
		{"31/02", "", "", false},
		{"0", "", "", false},
		{"45000", "", "", false},
		{"x", "", "", false},
		{"Monday", "", "", false},
		{"0000-01-01", "", "", false},
		{"2023-02-30", "", "", false},
		// The dateutil fields, with datetime(1, 1, 1) as the default: a day,
		// month or year alone is in year 1 (and no Timestamp), and a text
		// dateutil reads under one default date only reads all the same.
		{"30th", "0001-01-30 00:00:00.000000000", "day", false},
		{"Jan 31", "0001-01-31 00:00:00.000000000", "day", false},
		{"1 March", "0001-03-01 00:00:00.000000000", "day", false},
		{"Jan", "0001-01-01 00:00:00.000000000", "month", false},
		{"March", "0001-03-01 00:00:00.000000000", "month", false},
		{"10:30", "0001-01-01 10:30:00.000000000", "minute", false},
		{"12:00", "0001-01-01 12:00:00.000000000", "minute", false},
		{"12:00 AM", "0001-01-01 00:00:00.000000000", "minute", false},
		// Complete dates and times.
		{"2024", "2024-01-01 00:00:00.000000000", "year", false},
		{"2023", "2023-01-01 00:00:00.000000000", "year", false},
		{"2023-03", "2023-03-01 00:00:00.000000000", "month", false},
		{"01/2023", "2023-01-01 00:00:00.000000000", "month", false},
		{"March 2023", "2023-03-01 00:00:00.000000000", "month", false},
		{"2023Q1", "2023-01-01 00:00:00.000000000", "quarter", false},
		{"2023-03-15", "2023-03-15 00:00:00.000000000", "day", false},
		{"Mar 15 2023", "2023-03-15 00:00:00.000000000", "day", false},
		{"20230315", "2023-03-15 00:00:00.000000000", "day", false},
		{"1 March 2023", "2023-03-01 00:00:00.000000000", "day", false},
		{"Feb 29 2024", "2024-02-29 00:00:00.000000000", "day", false},
		{"2023-03-15 10:30", "2023-03-15 10:30:00.000000000", "minute", false},
		{"2023-03-15 10:30:00", "2023-03-15 10:30:00.000000000", "second", false},
		{"2023-03-15 12 PM", "2023-03-15 12:00:00.000000000", "hour", false},
		{"2023-03-15 10:30:00.123", "2023-03-15 10:30:00.123000000", "millisecond", false},
		{"2023-03-15 10:30:00.1234", "2023-03-15 10:30:00.123400000", "microsecond", false},
		{"2023-03-15 10:30:00.123456", "2023-03-15 10:30:00.123456000", "microsecond", false},
		// A date-first ISO text is read by numpy's parser, which keeps nine
		// digits; dateutil (and a time-only text) keeps six.
		{"2023-03-15 10:30:00.1234567", "2023-03-15 10:30:00.123456700", "nanosecond", false},
		{"2023-03-15 10:30:00.123456789", "2023-03-15 10:30:00.123456789", "nanosecond", false},
		{"2023-03-15 00:00:00.000000001", "2023-03-15 00:00:00.000000001", "nanosecond", false},
		{"20230315 10:30:00.123456789", "2023-03-15 10:30:00.123456789", "nanosecond", false},
		{"2023-3-15 10:30:00.123456789", "2023-03-15 10:30:00.123456789", "nanosecond", false},
		{"10:30:00.123456789", "0001-01-01 10:30:00.123456000", "microsecond", false},
		{"15/03/2023 10:30:00.123456789", "2023-03-15 10:30:00.123456000", "microsecond", false},
		// A time zone: pandas attaches it, a naive Index is no match.
		{"2023-03-15T10:30:00Z", "2023-03-15 10:30:00.000000000", "second", true},
		{"2023-03-15 10:30:00+01:00", "2023-03-15 10:30:00.000000000", "second", true},
	} {
		moment, ok, err := parseDateKey(tc.text)
		if err != nil {
			t.Errorf("%q: %v", tc.text, err)
			continue
		}
		if tc.at == "" {
			if ok {
				t.Errorf("%q: read as %v %v, want no label", tc.text, moment.at, resolutionNames[moment.reso])
			}
			continue
		}
		if !ok {
			t.Errorf("%q: no label, want %v %v", tc.text, tc.at, tc.reso)
			continue
		}
		want, parseErr := time.Parse(dateKeyLayout, tc.at)
		if parseErr != nil {
			t.Fatalf("%q: %v", tc.at, parseErr)
		}
		if !moment.at.Equal(want) || resolutionNames[moment.reso] != tc.reso || moment.hasTZ != tc.tz {
			t.Errorf("%q: %v %v (tz %v), want %v %v (tz %v)", tc.text, moment.at.Format(dateKeyLayout),
				resolutionNames[moment.reso], moment.hasTZ, tc.at, tc.reso, tc.tz)
		}
	}
}

// TestDateKeyLookups is DatetimeIndex.get_loc for those keys on the indexes a
// workbook makes: a day-resolution one, a minute-resolution one and one label.
// A text coarser than the index's resolution is a partial date slice (a Series:
// found but not a value), a finer one an exact lookup.
//
// Expected values: Python 3.13.16 with pandas 2.2.3 (scratch/w3/probe_loc.py):
//
//	>>> index = pandas.DatetimeIndex(['2023-03-15', '2023-03-16'])
//	>>> index.get_loc('Mar 15 2023'), index.get_loc('2023-03'), index.get_loc('2023-03-15 10:30')
//	(0, slice(0, 2, None), KeyError('2023-03-15 10:30'))
//	>>> minute = pandas.DatetimeIndex(['2023-03-15 10:30', '2023-03-15 10:31'])
//	>>> minute.get_loc('2023-03-15'), minute.get_loc('2023-03-15 10:30:00.000'), minute.get_loc('2023-03-15 10:30:00.123')
//	(slice(0, 2, None), 0, KeyError('2023-03-15 10:30:00.123'))
func TestDateKeyLookups(t *testing.T) {
	day := func(values ...string) *Frame {
		labels := make([]Value, len(values))
		for index, value := range values {
			at, err := time.ParseInLocation("2006-01-02 15:04:05", value, time.UTC)
			if err != nil {
				t.Fatal(err)
			}
			labels[index] = Value{Kind: Time, T: at}
		}
		return &Frame{Labels: labels, Index: DatetimeIndex}
	}
	indexes := map[string]*Frame{
		"day":    day("2023-03-15 00:00:00", "2023-03-16 00:00:00"),
		"minute": day("2023-03-15 10:30:00", "2023-03-15 10:31:00"),
		"year":   day("2023-03-15 00:00:00"),
	}
	for _, tc := range []struct {
		index    string
		key      string
		position int
		outcome  string // "absent", "slice" or "column"
	}{
		{"day", "31", 0, "absent"},
		{"day", "Feb 29", 0, "absent"},
		{"day", "29 febbraio", 0, "absent"},
		{"day", "30 Feb", 0, "absent"},
		{"day", "2024", 0, "absent"},
		{"day", "Jan", 0, "absent"},
		{"day", "10:30", 0, "absent"},
		{"day", "31/02", 0, "absent"},
		{"day", "30th", 0, "absent"},
		{"day", "1 March 2023", 0, "absent"},
		{"day", "01/2023", 0, "absent"},
		{"day", "12:00 AM", 0, "absent"},
		{"day", "2023-03-15 10:30", 0, "absent"},
		{"day", "2023-03-15 10:30:00.123", 0, "absent"},
		{"day", "2023-03-15 10:30:00.123456789", 0, "absent"},
		{"day", "10:30:00.123456789", 0, "absent"},
		{"day", "15/03/2023 10:30:00.123456789", 0, "absent"},
		{"day", "2023-03-15T10:30:00Z", 0, "absent"},
		{"day", "March 2023", 0, "slice"},
		{"day", "2023", 0, "slice"},
		{"day", "2023-03", 0, "slice"},
		{"day", "2023Q1", 0, "slice"},
		{"day", "Mar 15 2023", 0, "column"},
		{"day", "2023-03-15", 0, "column"},
		{"day", "20230315", 0, "column"},
		{"day", "2023-03-15 00:00:00", 0, "column"},
		{"minute", "2023-03-15 10:30", 0, "column"},
		{"minute", "2023-03-15 10:30:00", 0, "column"},
		{"minute", "2023-03-15 10:30:00.000", 0, "column"},
		{"minute", "2023-03-15 10:30:00.123", 0, "absent"},
		{"minute", "2023-03-15 10:31", 1, "column"},
		{"minute", "2023-03-15", 0, "slice"},
		{"minute", "Mar 15 2023", 0, "slice"},
		{"minute", "2023-03-15 00:00:00", 0, "absent"},
		{"year", "Mar 15 2023", 0, "column"},
		{"year", "2023-03-15 00:00:00", 0, "column"},
		{"year", "2023-03-14", 0, "absent"},
	} {
		position, found, raises, err := indexes[tc.index].Lookup(tc.key)
		if err != nil {
			t.Errorf("%s %q: %v", tc.index, tc.key, err)
			continue
		}
		switch tc.outcome {
		case "absent":
			if found || raises {
				t.Errorf("%s %q: found %v, raises %v, want absent", tc.index, tc.key, found, raises)
			}
		case "slice":
			if !found || !raises {
				t.Errorf("%s %q: found %v, raises %v, want a partial slice", tc.index, tc.key, found, raises)
			}
		case "column":
			if !found || raises || position != tc.position {
				t.Errorf("%s %q: %d, found %v, raises %v, want column %d", tc.index, tc.key, position, found, raises,
					tc.position)
			}
		}
	}
}
