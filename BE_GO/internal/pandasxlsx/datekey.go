package pandasxlsx

import (
	"math"
	"math/big"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pydateutil"
)

// resolution is pandas' Resolution: a datetime's grain. The order is pandas':
// a coarser resolution compares greater, and a key coarser than the Index's
// resolution is a partial date slice (DatetimeIndex.get_loc), not a label
// lookup.
type resolution int

const (
	resoNano resolution = iota
	resoMicro
	resoMilli
	resoSecond
	resoMinute
	resoHour
	resoDay
	resoMonth
	resoQuarter
	resoYear
)

// natStrings is pandas' c_nat_strings: the texts that parse as NaT.
var natStrings = map[string]bool{"NaT": true, "NAT": true, "nan": true, "NaN": true, "NAN": true, "nat": true}

// keyMoment is a text key's datetime: the instant, its grain, and whether it
// is NaT (a key that parses as a missing value).
type keyMoment struct {
	at    time.Time
	reso  resolution
	isNaT bool
	hasTZ bool
}

// parseDateKey is the string branch of DatetimeIndex._parse_with_reso and
// datetime's get_loc: pandas' parse_datetime_string_with_reso, for the texts
// every path models. ok is false where pandas raises (the key is no label: a
// KeyError); the error is ErrWorkbookNotPorted where the text is one this
// package cannot read (dateutil reads it under pandas' default but not under
// another, which no text of a real workbook does).
//
// The text is read with dateutil (internal/pydateutil), which is pandas'
// fallback, and the grain is the finest field the text itself sets: the same
// datetime is read three times with three defaults that differ in every field,
// and a date field the text sets keeps its value across all three. pydateutil
// fills a time the text does not set from midnight whatever the default, so
// the time grain is read from the text (timeGrain). dateutil's two-digit-year
// pivot is the cutover year of the moment of the lookup; pandas' is the year
// its dateutil was imported, which differ only across a New Year.
func parseDateKey(text string) (keyMoment, bool, error) {
	var moment keyMoment
	if !looksLikeDatetime(text) {
		return moment, false, nil // pandas: 'not likely a datetime'
	}
	if natStrings[text] {
		return keyMoment{isNaT: true, reso: resoNano}, true, nil
	}
	if moment, ok := quarterKey(text); ok {
		return moment, true, nil
	}
	if moment, ok := basicDateKey(text); ok {
		return moment, true, nil
	}
	defaults := []time.Time{
		time.Date(1, 1, 1, 0, 0, 0, 0, time.UTC),
		time.Date(2, 2, 2, 2, 2, 2, 2, time.UTC),
		time.Date(3, 3, 3, 3, 3, 3, 3, time.UTC),
	}
	values := make([]pydateutil.Result, len(defaults))
	for index, fallback := range defaults {
		result, err := pydateutil.ParseWithPivot(text, fallback, time.Now().Year(), time.UTC)
		if err != nil {
			if _, read := err.(*pydateutil.Error); !read {
				return moment, false, err
			}
			if index == 0 {
				return moment, false, nil // dateutil does not read the text
			}
			return moment, false, ErrWorkbookNotPorted
		}
		values[index] = result
	}
	at := values[0]
	moment = keyMoment{at: time.Date(at.Year, time.Month(at.Month), at.Day, at.Hour, at.Minute, at.Second,
		at.Microsecond*1000, time.UTC), hasTZ: at.HasTZ, reso: resoYear}
	fields := []struct {
		reso            resolution
		one, two, three int
	}{
		{resoYear, values[0].Year, values[1].Year, values[2].Year},
		{resoMonth, values[0].Month, values[1].Month, values[2].Month},
		{resoDay, values[0].Day, values[1].Day, values[2].Day},
	}
	for _, field := range fields {
		if field.one == field.two && field.two == field.three {
			moment.reso = field.reso
		}
	}
	grain, hasTime, nanos := timeGrain(text, moment.at)
	if hasTime && grain < moment.reso {
		moment.reso = grain
	}
	if moment.reso == resoNano {
		// pandas keeps the nanoseconds dateutil drops: the digits past the
		// sixth of a fraction of a second.
		moment.at = moment.at.Add(time.Duration(nanos) * time.Nanosecond)
	}
	return moment, true, nil
}

// timeGrain is the grain of the time of day a text writes: the hour, the
// minute ('H:MM'), the second ('H:MM:SS') or, with a fraction, the
// millisecond, microsecond or nanosecond; nanos is the fraction's digits past
// the sixth. A text without a time has none, and a time the text leaves
// implicit is the hour. pydateutil fills a time it does not read from
// midnight whatever the default, so the grain is read from the text.
func timeGrain(text string, at time.Time) (grain resolution, hasTime bool, nanos int) {
	if at.Hour() != 0 || at.Minute() != 0 || at.Second() != 0 || at.Nanosecond() != 0 {
		hasTime = true
	}
	for index := 0; index+1 < len(text) && !hasTime; index++ {
		if text[index] == ':' {
			hasTime = true
		}
		if c := text[index] | 0x20; c == 'a' || c == 'p' {
			if next := text[index+1] | 0x20; next == 'm' {
				hasTime = true
			}
		}
	}
	if !hasTime {
		return resoDay, false, 0
	}
	colons, fraction := 0, 0
	for index := 0; index < len(text); index++ {
		switch text[index] {
		case ':':
			colons++
		case '.':
			fraction = 0
			digits := make([]byte, 0, 9)
			for at := index + 1; at < len(text) && text[at] >= '0' && text[at] <= '9'; at++ {
				fraction++
				if len(digits) < 9 {
					digits = append(digits, text[at])
				}
			}
			nanos = 0
			if len(digits) > 6 {
				for _, digit := range digits[6:] {
					nanos = nanos*10 + int(digit-'0')
				}
				for pad := len(digits); pad < 9; pad++ {
					nanos *= 10
				}
			}
		}
	}
	switch {
	case fraction > 6:
		return resoNano, true, nanos
	case fraction > 3:
		return resoMicro, true, 0
	case fraction > 0:
		return resoMilli, true, 0
	case colons >= 2:
		return resoSecond, true, 0
	case colons == 1:
		return resoMinute, true, 0
	}
	return resoHour, true, 0
}

// looksLikeDatetime is pandas' _does_string_look_like_datetime: a text that
// starts with '0' is datelike, the single letters dateutil would read as a
// weekday or meridiem are not, and a text that is a number below 1000 is not.
func looksLikeDatetime(text string) bool {
	if text == "" {
		return true
	}
	if text[0] == '0' {
		return true
	}
	switch text {
	case "a", "A", "m", "M", "p", "P", "t", "T":
		return false
	}
	if value, _, ok, _ := preciseStrtod(text); ok && value < 1000 {
		return false
	}
	return true
}

// quarterKey is pandas' quarter forms: 2Q2005, 2Q05, 2005Q1, 05Q1 (the '-'
// after the Q is allowed), read as the quarter's first month.
func quarterKey(text string) (keyMoment, bool) {
	var moment keyMoment
	value := strings.ToUpper(text)
	length := len(value)
	if length < 4 || length > 7 {
		return moment, false
	}
	at := strings.Index(value[1:min(len(value), 6)], "Q") + 1
	if at < 1 || at > 5 {
		return moment, false
	}
	digit := func(text string) (int, bool) {
		number, err := strconv.Atoi(text)
		return number, err == nil && number >= 0
	}
	year, quarter, ok := 0, 0, false
	switch at {
	case 1:
		quarter, ok = digit(value[:1])
		if length == 4 || length == 5 && value[2] == '-' {
			year, ok = digit(value[length-2:])
			year += 2000
		} else if length == 6 || length == 7 && value[2] == '-' {
			year, ok = digit(value[length-4:])
		} else {
			ok = false
		}
	case 2, 3:
		if length == 4 || length == 5 && value[at-1] == '-' {
			quarter, ok = digit(value[length-1:])
			year, ok2 := digit(value[:2])
			ok = ok && ok2
			year += 2000
		}
	case 4, 5:
		if length == 6 || length == 7 && value[at-1] == '-' {
			quarter, ok = digit(value[length-1:])
			year, ok = digit(value[:4])
		}
	}
	if !ok || quarter < 1 || quarter > 4 || year < 1 || year > 9999 {
		return moment, false
	}
	moment.at = time.Date(year, time.Month((quarter-1)*3+1), 1, 0, 0, 0, 0, time.UTC)
	moment.reso = resoQuarter
	return moment, true
}

// basicDateKey is numpy's ISO 8601 basic format, which pandas' parse path
// reads before dateutil: YYYYMMDD and YYYYMMDDTHHMMSS[.fffffffff].
func basicDateKey(text string) (keyMoment, bool) {
	var moment keyMoment
	digits := text
	fraction := ""
	if at := strings.IndexByte(digits, '.'); at >= 0 {
		digits, fraction = digits[:at], digits[at+1:]
	}
	digits = strings.Replace(digits, "T", "", 1)
	for index := 0; index < len(digits); index++ {
		if digits[index] < '0' || digits[index] > '9' {
			return moment, false
		}
	}
	if len(digits) != 8 && len(digits) != 14 {
		return moment, false
	}
	number := func(at, size int) int {
		value, _ := strconv.Atoi(digits[at : at+size])
		return value
	}
	moment.at = time.Date(number(0, 4), time.Month(number(4, 2)), number(6, 2), 0, 0, 0, 0, time.UTC)
	moment.reso = resoDay
	if len(digits) == 14 {
		moment.at = time.Date(number(0, 4), time.Month(number(4, 2)), number(6, 2), number(8, 2), number(10, 2),
			number(12, 2), 0, time.UTC)
		moment.reso = resoSecond
	}
	if fraction != "" {
		if len(fraction) > 9 {
			return moment, false
		}
		nanos, _ := strconv.Atoi(leftPad(fraction, 9)[:9])
		moment.at = moment.at.Add(time.Duration(nanos) * time.Nanosecond)
		switch {
		case len(fraction) <= 3:
			moment.reso = resoMilli
		case len(fraction) <= 6:
			moment.reso = resoMicro
		default:
			moment.reso = resoNano
		}
	}
	if moment.at.Year() != number(0, 4) || int(moment.at.Month()) != number(4, 2) || moment.at.Day() != number(6, 2) {
		return moment, false // datetime_new raises on an invalid date
	}
	return moment, true
}

// periodBounds is _parsed_string_to_bounds: the first and last nanosecond of
// the key's grain (the slice _partial_date_slice searches).
func periodBounds(at time.Time, reso resolution) (time.Time, time.Time) {
	start := at
	switch reso {
	case resoYear:
		start = time.Date(at.Year(), 1, 1, 0, 0, 0, 0, time.UTC)
		return start, start.AddDate(1, 0, 0).Add(-time.Nanosecond)
	case resoQuarter:
		quarter := time.Month((int(at.Month())-1)/3*3 + 1)
		start = time.Date(at.Year(), quarter, 1, 0, 0, 0, 0, time.UTC)
		return start, start.AddDate(0, 3, 0).Add(-time.Nanosecond)
	case resoMonth:
		start = time.Date(at.Year(), at.Month(), 1, 0, 0, 0, 0, time.UTC)
		return start, start.AddDate(0, 1, 0).Add(-time.Nanosecond)
	case resoDay:
		start = time.Date(at.Year(), at.Month(), at.Day(), 0, 0, 0, 0, time.UTC)
		return start, start.AddDate(0, 0, 1).Add(-time.Nanosecond)
	case resoHour:
		start = at.Truncate(time.Hour)
		return start, start.Add(time.Hour - time.Nanosecond)
	case resoMinute:
		start = at.Truncate(time.Minute)
		return start, start.Add(time.Minute - time.Nanosecond)
	case resoSecond:
		start = at.Truncate(time.Second)
		return start, start.Add(time.Second - time.Nanosecond)
	case resoMilli:
		start = at.Truncate(time.Millisecond)
		return start, start.Add(time.Millisecond - time.Nanosecond)
	case resoMicro:
		start = at.Truncate(time.Microsecond)
		return start, start.Add(time.Microsecond - time.Nanosecond)
	}
	return at, at
}

// dateResolution is DatetimeIndex._resolution_obj: the finest grain every label
// is a multiple of, never coarser than a day. NaT labels are skipped, and an
// index of NaT only is a day.
func (f *Frame) dateResolution() resolution {
	finest := resoDay
	for _, label := range f.Labels {
		if label.Kind != Time {
			continue
		}
		nanos := label.T.Nanosecond()
		grain := resoDay
		switch {
		case nanos%1000 != 0:
			grain = resoNano
		case nanos%1000000 != 0:
			grain = resoMicro
		case nanos != 0:
			grain = resoMilli
		case label.T.Second() != 0:
			grain = resoSecond
		case label.T.Minute() != 0:
			grain = resoMinute
		case label.T.Hour() != 0:
			grain = resoHour
		}
		if grain < finest {
			finest = grain
		}
	}
	return finest
}

// dateMonotonic is Index.is_monotonic_increasing for the labels: NaT makes an
// index non-monotonic.
func (f *Frame) dateMonotonic() bool {
	previous := time.Time{}
	first := true
	for _, label := range f.Labels {
		if label.Kind == NaT {
			return false
		}
		if label.Kind != Time {
			continue
		}
		if !first && label.T.Before(previous) {
			return false
		}
		previous, first = label.T, false
	}
	return true
}

// timedeltaKey is TimedeltaIndex.get_loc's text branch: pandas' Timedelta
// parses the text into nanoseconds, which must be one of the labels.
func (f *Frame) timedeltaKey(text string) (position int, found, raises bool, err error) {
	nanos, ok := parseTimedeltaKey(text)
	if !ok {
		return 0, false, false, nil // Timedelta raises: a KeyError
	}
	if nanos == nil {
		// NaT: a NaT label, and only one.
		for index, label := range f.Labels {
			if label.Kind == NaT {
				return index, true, false, nil
			}
		}
		return 0, false, false, nil
	}
	for index, label := range f.Labels {
		if label.Kind != Timedelta || label.D != time.Duration(nanos.Int64()) {
			continue
		}
		return index, true, false, nil
	}
	return 0, false, false, nil
}

// parseTimedeltaKey is pandas' Timedelta(text) -> nanoseconds. ok is false
// where Timedelta raises (a KeyError for the caller); a nil value is NaT.
func parseTimedeltaKey(text string) (*big.Int, bool) {
	if text == "" || natStrings[text] {
		return nil, true // pandas: parse_timedelta_string("") is NaT
	}
	if len(text) > 0 && text[0] == 'P' || len(text) > 1 && text[:2] == "-P" {
		value, ok := parseISODuration(text)
		return value, ok
	}
	value, ok := parseTimedeltaString(text)
	return value, ok
}

// parseTimedeltaString is pandas' parse_timedelta_string: a sequence of
// number+unit pairs ('1 days 2 hours'), an hh:mm:ss[.fff] field, or a bare
// integer read as nanoseconds; a leading '-' negates the fields after it.
func parseTimedeltaString(text string) (*big.Int, bool) {
	var (
		result     = new(big.Int)
		negative   bool
		haveDot    bool
		haveValue  bool
		haveHHMMSS bool
		current    string
		number     []byte
		frac       []byte
		unit       []byte
	)
	// add is timedelta_as_neg(timedelta_from_spec(number, frac, unit), neg).
	add := func(number, frac, unit []byte) bool {
		value, ok := timedeltaFromSpec(string(number), string(frac), string(unit))
		if !ok {
			return false
		}
		if negative {
			value.Neg(value)
		}
		result.Add(result, value)
		return true
	}
	for index := 0; index < len(text); index++ {
		c := text[index]
		switch {
		case c == ' ' || c == ',':
		case c == '+':
		case c == '-':
			if negative || haveValue || haveHHMMSS {
				return nil, false // only leading negative signs are allowed
			}
			negative = true
		case c >= '0' && c <= '9':
			switch {
			case haveDot && len(unit) > 0:
				number = append(number, c)
				haveDot = false
			case haveDot:
				frac = append(frac, c)
			case len(unit) == 0:
				number = append(number, c)
			default:
				if !add(number, frac, unit) {
					return nil, false
				}
				unit, number, frac = nil, []byte{c}, nil
			}
		case c == ':':
			if haveValue {
				negative = false
			}
			if len(number) == 0 {
				return nil, false // expecting hh:mm:ss format
			}
			micros := int64(0)
			switch current {
			case "":
				current = "h"
				micros = 3600 * 1000000
			case "h":
				current = "m"
				micros = 60 * 1000000
			case "m":
				current = "s"
				micros = 1000000
			}
			if !addNanos(result, number, micros*1000, negative) {
				return nil, false
			}
			haveHHMMSS = true
			unit, number = nil, nil
		case c == '.':
			if len(number) > 0 && current != "" {
				if current != "m" {
					return nil, false // expected hh:mm:ss format before .
				}
				if !addNanos(result, number, 1000000000, negative) {
					return nil, false
				}
				haveValue = true
				unit, number, frac = nil, nil, nil
			}
			haveDot = true
		default:
			unit = append(unit, c)
			haveValue = true
			haveDot = false
		}
	}
	switch {
	case haveDot && len(unit) > 0:
		if !add(number, frac, unit) {
			return nil, false
		}
	case haveDot:
		if (len(number) > 0 || len(frac) > 0) && len(unit) == 0 && current == "" {
			return nil, false // no units specified
		}
		if len(frac) > 9 {
			frac = frac[:9]
		}
		var digits big.Int
		if len(frac) > 0 {
			digits.SetString(string(frac), 10)
			scale := int64(1)
			switch {
			case len(frac) <= 3:
				scale = int64(math.Pow10(3-len(frac))) * 1000000
			case len(frac) <= 6:
				scale = int64(math.Pow10(6-len(frac))) * 1000
			default:
				scale = int64(math.Pow10(9 - len(frac)))
			}
			digits.Mul(&digits, big.NewInt(scale))
		}
		if negative {
			digits.Neg(&digits)
		}
		result.Add(result, &digits)
	case current != "":
		if current != "m" {
			return nil, false // expected hh:mm:ss format
		}
		if !addNanos(result, number, 1000000000, negative) {
			return nil, false
		}
	case len(unit) > 0:
		if len(number) == 0 {
			return nil, false // unit abbreviation w/o a number
		}
		if !add(number, frac, unit) {
			return nil, false
		}
	case len(number) == 0:
		return nil, false // symbols w/o a number
	default:
		if haveValue {
			return nil, false // have leftover units
		}
		if !add(number, nil, []byte("ns")) {
			return nil, false
		}
	}
	if !result.IsInt64() {
		return nil, false // OutOfBoundsTimedelta
	}
	return result, true
}

// addNanos adds int(number) * scale nanoseconds (timedelta_as_neg of an
// hh:mm:ss field), refusing a value outside int64.
func addNanos(result *big.Int, number []byte, scale int64, negative bool) bool {
	digits, ok := new(big.Int).SetString(string(number), 10)
	if !ok {
		return false
	}
	digits.Mul(digits, big.NewInt(scale))
	if negative {
		digits.Neg(digits)
	}
	result.Add(result, digits)
	return result.IsInt64()
}

// timedeltaFromSpec is pandas' timedelta_from_spec: float(number.frac) cast
// through the unit to nanoseconds ([int64(v)*m + int64((v-int64(v))*m)]).
func timedeltaFromSpec(number, frac, unit string) (*big.Int, bool) {
	scale, ok := timedeltaUnits[strings.ToLower(unit)]
	if !ok {
		return nil, false
	}
	if unit == "M" || unit == "Y" || unit == "y" {
		return nil, false // not an unambiguous duration
	}
	text := number
	if frac != "" {
		text += "." + frac
	} else {
		text += ".0"
	}
	value, err := strconv.ParseFloat(text, 64)
	if err != nil || math.IsNaN(value) || math.IsInf(value, 0) {
		return nil, false
	}
	whole := math.Trunc(value)
	part := (value - whole) * float64(scale)
	if math.Abs(whole) >= 9.3e18/float64(scale) {
		return nil, false // OutOfBoundsDatetime
	}
	total := new(big.Int).SetInt64(int64(whole))
	total.Mul(total, big.NewInt(scale))
	total.Add(total, big.NewInt(int64(part)))
	if !total.IsInt64() {
		return nil, false
	}
	return total, true
}

// timedeltaUnits is pandas' timedelta_abbrevs, in nanoseconds.
var timedeltaUnits = map[string]int64{
	"d": 86400 * 1000000000, "day": 86400 * 1000000000, "days": 86400 * 1000000000,
	"h": 3600 * 1000000000, "hour": 3600 * 1000000000, "hours": 3600 * 1000000000, "hr": 3600 * 1000000000,
	"m": 60 * 1000000000, "min": 60 * 1000000000, "minute": 60 * 1000000000, "minutes": 60 * 1000000000,
	"s": 1000000000, "sec": 1000000000, "second": 1000000000, "seconds": 1000000000,
	"ms": 1000000, "milli": 1000000, "millis": 1000000, "millisecond": 1000000, "milliseconds": 1000000,
	"us": 1000, "micro": 1000, "micros": 1000, "microsecond": 1000, "microseconds": 1000, "µs": 1000,
	"ns": 1, "nano": 1, "nanos": 1, "nanosecond": 1, "nanoseconds": 1,
	"w": 604800 * 1000000000, "week": 604800 * 1000000000, "weeks": 604800 * 1000000000,
}

// parseISODuration is pandas' parse_iso_format_string: P[n]W[n]D[n]H[n]M[n]S
// with a fraction of a second read as ms, us or ns.
func parseISODuration(text string) (*big.Int, bool) {
	result := new(big.Int)
	sign := int64(1)
	if text[0] == '-' {
		sign = -1
		text = text[1:]
	}
	var (
		number   []byte
		unit     []byte
		decimal  = "ms"
		position int
		haveDot  bool
		haveVal  bool
		negative bool
	)
	add := func(unit string) bool {
		value, ok := timedeltaFromSpec(string(number), "", unit)
		if !ok {
			return false
		}
		if negative {
			value.Neg(value)
		}
		result.Add(result, value)
		return true
	}
	for index := 0; index < len(text); index++ {
		c := text[index]
		switch {
		case c >= '0' && c <= '9':
			haveVal = true
			if haveDot {
				if position == 3 && decimal != "ns" {
					unit = append(unit, decimal...)
					if decimal == "ms" {
						decimal = "us"
					} else {
						decimal = "ns"
					}
					position = 0
				}
				position++
			}
			if len(unit) == 0 {
				number = append(number, c)
				continue
			}
			if !add(string(unit)) {
				return nil, false
			}
			negative = false
			unit, number = nil, []byte{c}
		case c == 'P' || c == 'T' || c == '+':
		case c == '-':
			if negative || haveVal {
				return nil, false
			}
			negative = true
		case c == 'W' || c == 'D' || c == 'H' || c == 'M':
			if (c == 'H' || c == 'M') && len(number) > 2 {
				return nil, false
			}
			name := string(c)
			if c == 'H' {
				name = "h"
			} else if c == 'M' {
				name = "min"
			}
			unit = append(unit, name...)
			if !add(string(unit)) {
				return nil, false
			}
			negative = false
			unit, number = nil, nil
		case c == '.':
			if len(number) > 0 {
				if !add("s") {
					return nil, false
				}
				unit, number = nil, nil
			}
			haveDot = true
		case c == 'S':
			if haveDot {
				if len(number) == 0 || position > 3 {
					return nil, false
				}
				for pad := 3 - position; pad > 0; pad-- {
					number = append(number, '0')
				}
				if !add(decimal) {
					return nil, false
				}
			} else if !add("s") {
				return nil, false
			}
			number, unit = nil, nil
		default:
			return nil, false
		}
	}
	if !haveVal {
		return nil, false
	}
	if sign < 0 {
		result.Neg(result)
	}
	if !result.IsInt64() {
		return nil, false
	}
	return result, true
}

// keyLookup is DatetimeIndex.get_loc for a text key: an exact label, a partial
// date slice (a Series, which the importer cannot read), or no label at all.
func (f *Frame) dateKey(key string) (position int, found, raises bool, err error) {
	moment, ok, err := parseDateKey(key)
	if err != nil || !ok {
		return 0, false, false, err
	}
	if moment.isNaT {
		for index, label := range f.Labels {
			if label.Kind == NaT {
				return index, true, false, nil
			}
		}
		return 0, false, false, nil
	}
	if moment.hasTZ {
		return 0, false, false, nil // a timezone-aware key against a naive Index
	}
	if !inTimestampBounds(moment.at) {
		return 0, false, false, nil // Timestamp(parsed) raises OutOfBoundsDatetime
	}
	if moment.reso <= f.dateResolution() {
		for index, label := range f.Labels {
			if label.Kind == Time && label.T.Equal(moment.at) {
				return index, true, false, nil
			}
		}
		return 0, false, false, nil
	}
	// A partial date slice: `key in keys` finds it (a slice or a mask), and
	// row[key] is a Series.
	if flat, ok := f.dateBounds(); ok {
		low, high := periodBounds(moment.at, moment.reso)
		if high.Before(flat.first) || low.After(flat.last) {
			return 0, false, false, nil // _partial_date_slice raises KeyError
		}
	}
	return 0, true, true, nil
}

// dateBounds is the first and last label of a monotonic DatetimeIndex.
func (f *Frame) dateBounds() (bounds struct{ first, last time.Time }, ok bool) {
	for _, label := range f.Labels {
		if label.Kind != Time {
			continue
		}
		if !ok {
			bounds.first = label.T
		}
		bounds.last, ok = label.T, true
	}
	if !ok || !f.dateMonotonic() {
		return bounds, false
	}
	return bounds, true
}
