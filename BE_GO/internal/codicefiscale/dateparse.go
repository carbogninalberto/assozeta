package codicefiscale

import (
	"errors"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pydateutil"
)

// This file ports codicefiscale._get_date(str) and the part of
// dateutil.parser.parse (python-dateutil 2.9.0.post0) it reaches for slugs
// made of ASCII digit groups joined by '-'. A slug containing a letter goes
// through the full grammar in internal/pydateutil.

// errDateParse marks every failure of _get_date: the ValueError it turns into
// None and the OverflowError it lets through end the same way for encode.
var errDateParse = errors.New("codicefiscale: unparsable date")

const (
	// pyIntMaxStrDigits is Python's default sys.get_int_max_str_digits().
	pyIntMaxStrDigits = 4300
	// saturated stands for any integer too large to be a valid date field;
	// such values always make the date invalid, as in Python.
	saturated int64 = 1 << 40
)

// unset is Python's None for the integer fields below.
const unset = -1

// getDate ports _get_date for a non-datetime argument. It returns
// errDateParse when Python returns None or raises, and ErrUnsupported when the
// slug is outside the ported subset.
func getDate(date string, now time.Time) (time.Time, error) {
	if date == "" {
		return time.Time{}, errDateParse
	}
	slug, err := slugify(date)
	if err != nil {
		return time.Time{}, err
	}
	var groups []string
	if slug != "" {
		groups = strings.Split(slug, "-")
	}
	yearFirst := len(groups) > 0 && len(groups[0]) == 4
	for i := 0; i < len(slug); i++ {
		if c := slug[i]; c != '-' && !isASCIIDigit(c) {
			return parseLetterSlug(slug, yearFirst, now)
		}
	}
	return parseDigitSlug(groups, !yearFirst, yearFirst, now)
}

// parseLetterSlug is date_parser.parse(slug, parserinfo=parserinfo(yearfirst
// or dayfirst)) for a slug with letters (month names, an ISO "t" time): the
// whole dateutil grammar. The process zone is UTC, as Django sets TZ from
// TIME_ZONE; every exception Python raises is an invalid date for the caller.
func parseLetterSlug(slug string, yearFirst bool, now time.Time) (time.Time, error) {
	parse := pydateutil.ParseDayFirst
	if yearFirst {
		parse = pydateutil.ParseYearFirst
	}
	result, err := parse(slug, now, time.UTC)
	var raised *pydateutil.Error
	switch {
	case errors.As(err, &raised):
		return time.Time{}, errDateParse
	case err != nil:
		return time.Time{}, ErrUnsupported
	}
	return time.Date(result.Year, time.Month(result.Month), result.Day, result.Hour, result.Minute, result.Second,
		result.Microsecond*1000, time.UTC), nil
}

// pyInt is int(s) for an ASCII digit string, failing like Python above the
// integer string conversion limit and saturating huge values.
func pyInt(s string) (int64, error) {
	if len(s) > pyIntMaxStrDigits {
		return 0, errDateParse
	}
	return digitsValue(s), nil
}

// digitsValue is the value of an ASCII digit string, saturated.
func digitsValue(s string) int64 {
	var v int64
	for i := 0; i < len(s); i++ {
		v = v*10 + int64(s[i]-'0')
		if v >= saturated {
			return saturated
		}
	}
	return v
}

// ymdList ports dateutil.parser._ymd for numeric values only: month and day
// labels never occur for digit tokens.
type ymdList struct {
	values           []int64
	centurySpecified bool
	ystridx          int
}

// appendString is _ymd.append(str_value, label).
func (y *ymdList) appendString(s string, yearLabel bool) error {
	if len(s) > 2 {
		y.centurySpecified = true
		yearLabel = true
	}
	v, err := pyInt(s)
	if err != nil {
		return err
	}
	return y.push(v, yearLabel)
}

// appendNumber is _ymd.append(decimal_value) for the trailing token.
func (y *ymdList) appendNumber(v int64) error {
	yearLabel := false
	if v > 100 {
		y.centurySpecified = true
		yearLabel = true
	}
	return y.push(v, yearLabel)
}

func (y *ymdList) push(v int64, yearLabel bool) error {
	y.values = append(y.values, v)
	if yearLabel {
		if y.ystridx != unset {
			return errDateParse // "Year is already set"
		}
		y.ystridx = len(y.values) - 1
	}
	return nil
}

// resolve ports _ymd.resolve_ymd with mstridx and dstridx always None.
func (y *ymdList) resolve(yearFirst, dayFirst bool) (year, month, day int64, err error) {
	year, month, day = unset, unset, unset
	v := y.values
	if y.ystridx != unset && len(v) == 1 {
		return v[0], unset, unset, nil
	}
	switch len(v) {
	case 0:
	case 1:
		if v[0] > 31 {
			year = v[0]
		} else {
			day = v[0]
		}
	case 2:
		switch {
		case v[0] > 31:
			year, month = v[0], v[1]
		case v[1] > 31:
			month, year = v[0], v[1]
		case dayFirst && v[1] <= 12:
			day, month = v[0], v[1]
		default:
			month, day = v[0], v[1]
		}
	case 3:
		switch {
		case v[0] > 31 || y.ystridx == 0 || (yearFirst && v[1] <= 12 && v[2] <= 31):
			if dayFirst && v[2] <= 12 {
				year, day, month = v[0], v[1], v[2]
			} else {
				year, month, day = v[0], v[1], v[2]
			}
		case v[0] > 12 || (dayFirst && v[1] <= 12):
			day, month, year = v[0], v[1], v[2]
		default:
			month, day, year = v[0], v[1], v[2]
		}
	default:
		return 0, 0, 0, errDateParse // "More than three YMD values"
	}
	return year, month, day, nil
}

// dateResult holds the parser result fields that reach the naive datetime.
type dateResult struct {
	hour, minute, second int64
}

// parseDigitSlug ports parser.parse(slug, parserinfo(dayfirst/yearfirst))
// followed by .replace(tzinfo=None) for the token stream
// groups[0], "-", groups[1], "-", ...
func parseDigitSlug(groups []string, dayFirst, yearFirst bool, now time.Time) (time.Time, error) {
	var tokens []string
	for i, g := range groups {
		if i > 0 {
			tokens = append(tokens, "-")
		}
		tokens = append(tokens, g)
	}
	n := len(tokens)
	res := dateResult{hour: unset, minute: unset, second: unset}
	ymd := ymdList{ystridx: unset}

	for i := 0; i < n; i++ {
		if tokens[i] != "-" {
			next, err := parseNumericToken(tokens, i, &ymd, &res)
			if err != nil {
				return time.Time{}, err
			}
			i = next
			continue
		}
		if res.hour == unset {
			continue // a jump token
		}
		// Numeric timezone offset: "-HHMM" or "-H[H]"; it only sets
		// tzoffset, which the final replace(tzinfo=None) discards.
		if i+1 >= n {
			return time.Time{}, errDateParse
		}
		if l := len(tokens[i+1]); l != 4 && l > 2 {
			return time.Time{}, errDateParse
		}
		i++
	}

	year, month, day, err := ymd.resolve(yearFirst, dayFirst)
	if err != nil {
		return time.Time{}, err
	}
	if year == unset && month == unset && day == unset && res.hour == unset && res.minute == unset && res.second == unset {
		return time.Time{}, errDateParse // "String does not contain a date"
	}
	if year != unset {
		year = convertYear(year, ymd.centurySpecified, int64(now.Year()))
	}
	return buildNaive(year, month, day, res, now)
}

// parseNumericToken ports parser._parse_numeric_token for a digit token whose
// neighbours are "-" tokens. It returns the index of the last consumed token.
func parseNumericToken(tokens []string, idx int, ymd *ymdList, res *dateResult) (int, error) {
	s := tokens[idx]
	n := len(tokens)
	var err error
	switch l := len(s); {
	case len(ymd.values) == 3 && (l == 2 || l == 4) && res.hour == unset:
		// 19990101T23[59]
		res.hour = digitsValue(s[:2])
		if l == 4 {
			res.minute = digitsValue(s[2:])
		}
	case l == 6:
		if len(ymd.values) == 0 { // YYMMDD
			for _, part := range []string{s[:2], s[2:4], s[4:]} {
				if err = ymd.appendString(part, false); err != nil {
					return 0, err
				}
			}
		} else { // HHMMSS
			res.hour = digitsValue(s[:2])
			res.minute = digitsValue(s[2:4])
			res.second = digitsValue(s[4:])
		}
	case l == 8 || l == 12 || l == 14: // YYYYMMDD[HHMM[SS]]
		for i, part := range []string{s[:4], s[4:6], s[6:8]} {
			if err = ymd.appendString(part, i == 0); err != nil {
				return 0, err
			}
		}
		if l > 8 {
			res.hour = digitsValue(s[8:10])
			res.minute = digitsValue(s[10:12])
			if l > 12 {
				res.second = digitsValue(s[12:])
			}
		}
	case idx+1 < n: // tokens[idx+1] is "-": 01-01[-01]
		if err = ymd.appendString(s, false); err != nil {
			return 0, err
		}
		if err = ymd.appendString(tokens[idx+2], false); err != nil {
			return 0, err
		}
		if idx+3 < n {
			if err = ymd.appendString(tokens[idx+4], false); err != nil {
				return 0, err
			}
			idx += 2
		}
		idx += 2
	default: // last token: year, month or day, appended as an exact Decimal
		if err = ymd.appendNumber(digitsValue(s)); err != nil {
			return 0, err
		}
	}
	return idx, nil
}

// convertYear ports parserinfo.convertyear with time.localtime() = now.
func convertYear(year int64, centurySpecified bool, nowYear int64) int64 {
	if year < 100 && !centurySpecified {
		year += nowYear / 100 * 100
		if year >= nowYear+50 {
			year -= 100
		} else if year < nowYear-50 {
			year += 100
		}
	}
	return year
}

// buildNaive ports parser._build_naive with default = now at midnight.
func buildNaive(year, month, day int64, res dateResult, now time.Time) (time.Time, error) {
	now = now.UTC()
	if year == unset {
		year = int64(now.Year())
	}
	if month == unset {
		month = int64(now.Month())
	}
	if month < 1 || month > 12 {
		return time.Time{}, errDateParse // IllegalMonthError or replace()
	}
	if day == unset {
		day = int64(now.Day())
		if last := daysIn(year, month); day > last {
			day = last
		}
	}
	hour, minute, second := res.hour, res.minute, res.second
	if hour == unset {
		hour = 0
	}
	if minute == unset {
		minute = 0
	}
	if second == unset {
		second = 0
	}
	if year < 1 || year > 9999 || day < 1 || day > daysIn(year, month) ||
		hour > 23 || minute > 59 || second > 59 {
		return time.Time{}, errDateParse
	}
	return time.Date(int(year), time.Month(month), int(day), int(hour), int(minute), int(second), 0, time.UTC), nil
}

func daysIn(year, month int64) int64 {
	switch month {
	case 2:
		if year%4 == 0 && (year%100 != 0 || year%400 == 0) {
			return 29
		}
		return 28
	case 4, 6, 9, 11:
		return 30
	}
	return 31
}
