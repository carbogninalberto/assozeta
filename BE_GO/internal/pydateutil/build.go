package pydateutil

import (
	"fmt"
	"math"
	"math/big"
	"time"
)

// classCaughtValueError marks, inside this package, a ValueError that
// parser.parse re-raises as ParserError with the text appended.
const classCaughtValueError = "caught ValueError"

const (
	intStrLimitMessage = "Exceeds the limit (4300 digits) for integer string conversion; " +
		"use sys.set_int_max_str_digits() to increase the limit"
	cIntMessage = "Python int too large to convert to C int"
)

var maxCInt = small(math.MaxInt32)

// buildNaive is parser._build_naive with default = the given date at
// midnight: default.replace(**parsed fields), the day clipped to the month
// when the text has none, then the move to the parsed weekday.
func buildNaive(res *parsed, defYear, defMonth, defDay int) (Result, *Error) {
	day := res.day
	if day == nil {
		// monthrange(year, month) of the parsed-or-default year and month.
		year, month := small(int64(defYear)), small(int64(defMonth))
		if res.year != nil {
			year = res.year
		}
		if res.month != nil {
			month = res.month
		}
		if month.Sign() <= 0 || month.Cmp(small(12)) > 0 {
			// calendar.IllegalMonthError, a ValueError; its str() formats
			// the month and is subject to the integer conversion limit.
			digits := month.String()
			if len(digits) > maxStrDigits {
				return Result{}, &Error{Class: ClassValueError, Message: intStrLimitMessage}
			}
			return Result{}, &Error{Class: classCaughtValueError, Message: "bad month number " + digits + "; must be 1-12"}
		}
		if last := daysInMonth(year, month.Int64()); int64(defDay) > last {
			day = small(last) // repl['day']; res.day stays None
		}
	}

	// datetime.replace converts every argument to a C int, in order, before
	// it validates any of them.
	fields := [6]int64{int64(defYear), int64(defMonth), int64(defDay), 0, 0, 0}
	for i, value := range [6]*big.Int{res.year, res.month, day, res.hour, res.minute, res.second} {
		if value == nil {
			continue
		}
		if value.Cmp(maxCInt) > 0 {
			return Result{}, &Error{Class: ClassOverflowError, Message: cIntMessage}
		}
		fields[i] = value.Int64()
	}
	year, month, monthDay := fields[0], fields[1], fields[2]
	invalid := func(message string) (Result, *Error) {
		return Result{}, &Error{Class: classCaughtValueError, Message: message}
	}
	switch {
	case year < 1 || year > 9999:
		return invalid(fmt.Sprintf("year %d is out of range", year))
	case month < 1 || month > 12:
		return invalid("month must be in 1..12")
	case monthDay < 1 || monthDay > daysInMonth(small(year), month):
		return invalid("day is out of range for month")
	case fields[3] > 23:
		return invalid("hour must be in 0..23")
	case fields[4] > 59:
		return invalid("minute must be in 0..59")
	case fields[5] > 59:
		return invalid("second must be in 0..59")
	}

	if res.weekday >= 0 && res.day == nil {
		// naive + relativedelta(weekday=n): forward to that weekday, the
		// same day included.
		date := time.Date(int(year), time.Month(month), int(monthDay), 0, 0, 0, 0, time.UTC)
		current := (int(date.Weekday()) + 6) % 7 // Monday is 0
		date = date.AddDate(0, 0, (7-current+res.weekday)%7)
		if date.Year() > 9999 {
			return Result{}, &Error{Class: ClassOverflowError, Message: "date value out of range"}
		}
		year, month, monthDay = int64(date.Year()), int64(date.Month()), int64(date.Day())
	}
	return Result{Year: int(year), Month: int(month), Day: int(monthDay), Hour: int(fields[3]), Minute: int(fields[4]),
		Second: int(fields[5]), Microsecond: res.microsecond}, nil
}

// localZone summarises a zone: every abbreviation it uses and, when it has a
// single abbreviation and offset, that pair.
type localZone struct {
	fixed         bool
	abbreviation  string
	offset        int
	abbreviations map[string]bool
}

func inspectZone(local *time.Location) localZone {
	type pair struct {
		name   string
		offset int
	}
	pairs := map[pair]bool{}
	zone := localZone{abbreviations: map[string]bool{}}
	at := time.Date(1700, 1, 1, 0, 0, 0, 0, time.UTC).In(local)
	limit := time.Date(2200, 1, 1, 0, 0, 0, 0, time.UTC)
	for {
		name, offset := at.Zone()
		pairs[pair{name, offset}] = true
		zone.abbreviations[name] = true
		zone.abbreviation, zone.offset = name, offset
		_, end := at.ZoneBounds()
		if end.IsZero() || !end.Before(limit) || !end.After(at) {
			break
		}
		at = end
	}
	zone.fixed = len(pairs) == 1
	return zone
}

// buildAware is parser._build_tzaware without tzinfos.
func buildAware(naive Result, res *parsed, local *time.Location) (Result, error) {
	if res.hasTZName {
		// `res.tzname in time.tzname`: the name is one of the local zone's.
		zone := inspectZone(local)
		switch {
		case zone.fixed && zone.abbreviation == res.tzname:
			// tz.tzlocal() of a zone without daylight time.
			naive.HasTZ, naive.OffsetSeconds = true, int64(zone.offset)
			naive.TZName, naive.HasTZName = res.tzname, true
			return naive, nil
		case !zone.fixed && zone.abbreviations[res.tzname]:
			return Result{}, ErrUnsupported
		}
	}
	switch {
	case res.tzoffset == nil:
		// No zone, or a name dateutil does not understand
		// (UnknownTimezoneWarning): the naive datetime.
		return naive, nil
	case res.tzoffset.Sign() == 0:
		// tz.UTC
		naive.HasTZ, naive.TZName, naive.HasTZName = true, "UTC", true
		return naive, nil
	}
	// tz.tzoffset(name, seconds) builds timedelta(seconds=offset).
	days := new(big.Int).Div(res.tzoffset, small(86400)) // floor: the divisor is positive
	if !days.IsInt64() || days.Int64() > math.MaxInt32 || days.Int64() < math.MinInt32 {
		return Result{}, &Error{Class: ClassOverflowError, Message: cIntMessage}
	}
	if d := days.Int64(); d > 999999999 || d < -999999999 {
		return Result{}, &Error{Class: ClassOverflowError,
			Message: fmt.Sprintf("days=%d; must have magnitude <= 999999999", d)}
	}
	naive.HasTZ, naive.OffsetSeconds = true, res.tzoffset.Int64()
	naive.TZName, naive.HasTZName = res.tzname, res.hasTZName
	return naive, nil
}
