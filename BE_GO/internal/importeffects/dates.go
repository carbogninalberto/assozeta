package importeffects

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// This file ports django.utils.dateparse (which delegates to CPython's
// fromisoformat and falls back to Django's own regexes) and the str() forms of
// datetime.datetime, datetime.date and datetime.time.

// django.utils.dateparse's own patterns (ASCII text after Unicode decimal
// digits are mapped; Python's `$` also matches before a final newline).
var (
	datetimePattern = regexp.MustCompile(`^(\d{4})-(\d{1,2})-(\d{1,2})[T ](\d{1,2}):(\d{1,2})(?::(\d{1,2})(?:[.,](\d{1,6})\d{0,6})?)?\s*(Z|[+-]\d{2}(?::?\d{2})?)?$`)
	datePattern     = regexp.MustCompile(`^(\d{4})-(\d{1,2})-(\d{1,2})$`)
	timePattern     = regexp.MustCompile(`^(\d{1,2}):(\d{1,2})(?::(\d{1,2})(?:[.,](\d{1,6})\d{0,6})?)?$`)
)

// pyDate is a datetime.date.
type pyDate struct{ year, month, day int }

func (d pyDate) String() string {
	return fmt.Sprintf("%04d-%02d-%02d", d.year, d.month, d.day)
}

// pyTime is a datetime.time (naive: Django rejects aware times on save).
type pyTime struct{ hour, minute, second, micro int }

func (t pyTime) String() string {
	text := fmt.Sprintf("%02d:%02d:%02d", t.hour, t.minute, t.second)
	if t.micro != 0 {
		text += fmt.Sprintf(".%06d", t.micro)
	}
	return text
}

// pyMoment is a datetime.datetime: wall time t, and Naive when it has no
// tzinfo. offMicro holds the sub-second part of a fixed offset (CPython
// accepts offsets with fractional seconds).
type pyMoment struct {
	t        time.Time
	naive    bool
	offMicro int
}

func (m pyMoment) String() string {
	text := pythonNaive(m.t)
	if m.naive {
		return text
	}
	_, offset := m.t.Zone()
	sign := "+"
	if offset < 0 {
		sign, offset = "-", -offset
	}
	text += sign + pad2(offset/3600) + ":" + pad2(offset%3600/60)
	if offset%60 != 0 || m.offMicro != 0 {
		text += ":" + pad2(offset%60)
		if m.offMicro != 0 {
			text += fmt.Sprintf(".%06d", m.offMicro)
		}
	}
	return text
}

// utc is astimezone(UTC) of an aware moment, or the wall time of a naive one.
func (m pyMoment) utc() time.Time {
	if m.naive {
		return m.t
	}
	return m.t.UTC().Add(-time.Duration(m.offMicro) * time.Microsecond)
}

// parseDateTime is django.utils.dateparse.parse_datetime: fromisoformat, then
// Django's datetime_re; nil means "not well formatted". A well formatted but
// invalid value returns CPython's ValueError.
func parseDateTime(text string) (*pyMoment, error) {
	if moment, ok := fromISODateTime(text); ok {
		return moment, nil
	}
	groups := fallbackMatch(datetimePattern, text)
	if groups == nil {
		return nil, nil
	}
	atoi := func(s string) int { n, _ := strconv.Atoi(s); return n }
	year, month, day := atoi(groups[1]), atoi(groups[2]), atoi(groups[3])
	hour, minute := atoi(groups[4]), atoi(groups[5])
	second := 0
	if groups[6] != "" {
		second = atoi(groups[6])
	}
	micro := 0
	if groups[7] != "" {
		micro = atoi((groups[7] + "000000")[:6])
	}
	zone := groups[8]
	location := time.UTC
	naive := true
	if zone != "" {
		naive = false
		if zone != "Z" {
			offsetMinutes := atoi(zone[1:3]) * 60
			if len(zone) > 3 {
				offsetMinutes += atoi(zone[len(zone)-2:])
			}
			if zone[0] == '-' {
				offsetMinutes = -offsetMinutes
			}
			hours := offsetMinutes / 60
			if hours <= -24 || hours >= 24 {
				return nil, djangoError(fmt.Sprintf("offset must be a timedelta strictly between -timedelta(hours=24) and timedelta(hours=24), not %s", pythonTimedeltaRepr(offsetMinutes)))
			}
			location = time.FixedZone("", offsetMinutes*60)
		}
	}
	moment, err := newPyMoment(year, month, day, hour, minute, second, micro, location, naive)
	if err != nil {
		return nil, err
	}
	return &moment, nil
}

// newPyMoment builds datetime.datetime(...), with CPython's range errors.
func newPyMoment(year, month, day, hour, minute, second, micro int, location *time.Location, naive bool) (pyMoment, error) {
	if year < 1 || year > 9999 {
		return pyMoment{}, djangoError("year " + strconv.Itoa(year) + " is out of range")
	}
	if month < 1 || month > 12 {
		return pyMoment{}, djangoError("month must be in 1..12")
	}
	if day < 1 {
		return pyMoment{}, djangoError("day is out of range for month")
	}
	if hour < 0 || hour > 23 {
		return pyMoment{}, djangoError("hour must be in 0..23")
	}
	if minute < 0 || minute > 59 {
		return pyMoment{}, djangoError("minute must be in 0..59")
	}
	if second < 0 || second > 59 {
		return pyMoment{}, djangoError("second must be in 0..59")
	}
	if micro < 0 || micro > 999999 {
		return pyMoment{}, djangoError("microsecond must be in 0..999999")
	}
	if day > daysInMonth(year, month) {
		return pyMoment{}, djangoError("day is out of range for month")
	}
	return pyMoment{t: time.Date(year, time.Month(month), day, hour, minute, second, micro*1000, location), naive: naive}, nil
}

func daysInMonth(year, month int) int {
	return time.Date(year, time.Month(month)+1, 0, 0, 0, 0, 0, time.UTC).Day()
}

// pythonTimedeltaRepr is repr(datetime.timedelta(minutes=minutes)).
func pythonTimedeltaRepr(minutes int) string {
	return "datetime.timedelta(" + pythonTimedeltaTerms(int64(minutes)*60*int64(time.Second), 0) + ")"
}

// pythonTimedeltaTerms is the argument list of repr(datetime.timedelta).
func pythonTimedeltaTerms(totalNanos int64, micro int) string {
	const day = int64(24 * time.Hour)
	days := totalNanos / day
	rest := totalNanos % day
	if rest < 0 {
		rest += day
		days--
	}
	seconds := rest / int64(time.Second)
	microseconds := (rest%int64(time.Second))/int64(time.Microsecond) + int64(micro)
	if microseconds >= 1000000 {
		seconds++
		microseconds -= 1000000
	}
	parts := []string{}
	if days != 0 {
		parts = append(parts, fmt.Sprintf("days=%d", days))
	}
	if seconds != 0 || days == 0 {
		parts = append(parts, fmt.Sprintf("seconds=%d", seconds))
	}
	if microseconds != 0 {
		parts = append(parts, fmt.Sprintf("microseconds=%d", microseconds))
	}
	return strings.Join(parts, ", ")
}

// parseDate is django.utils.dateparse.parse_date.
func parseDate(text string) (*pyDate, error) {
	if date, ok := fromISODate(text); ok {
		return &date, nil
	}
	groups := fallbackMatch(datePattern, text)
	if groups == nil {
		return nil, nil
	}
	atoi := func(s string) int { n, _ := strconv.Atoi(s); return n }
	date := pyDate{year: atoi(groups[1]), month: atoi(groups[2]), day: atoi(groups[3])}
	if err := validatePyDate(date); err != nil {
		return nil, err
	}
	return &date, nil
}

func validatePyDate(date pyDate) error {
	if date.year < 1 || date.year > 9999 {
		return djangoError("year " + strconv.Itoa(date.year) + " is out of range")
	}
	if date.month < 1 || date.month > 12 {
		return djangoError("month must be in 1..12")
	}
	if date.day < 1 || date.day > daysInMonth(date.year, date.month) {
		return djangoError("day is out of range for month")
	}
	return nil
}

// parseTime is django.utils.dateparse.parse_time; nil means "not well
// formatted".
func parseTime(text string) (*pyTime, error) {
	if parsed, ok := fromISOTime(text); ok {
		return &parsed, nil
	}
	groups := fallbackMatch(timePattern, text)
	if groups == nil {
		return nil, nil
	}
	atoi := func(s string) int { n, _ := strconv.Atoi(s); return n }
	parsed := pyTime{hour: atoi(groups[1]), minute: atoi(groups[2])}
	if groups[3] != "" {
		parsed.second = atoi(groups[3])
	}
	if groups[4] != "" {
		parsed.micro = atoi((groups[4] + "000000")[:6])
	}
	if parsed.hour > 23 {
		return nil, djangoError("hour must be in 0..23")
	}
	if parsed.minute > 59 {
		return nil, djangoError("minute must be in 0..59")
	}
	if parsed.second > 59 {
		return nil, djangoError("second must be in 0..59")
	}
	return &parsed, nil
}

// fromISODateTime is datetime.fromisoformat.
func fromISODateTime(text string) (*pyMoment, bool) {
	date, rest, ok := fromISODatePrefix(text)
	if !ok {
		return nil, false
	}
	if rest == "" {
		return &pyMoment{t: time.Date(date.year, time.Month(date.month), date.day, 0, 0, 0, 0, time.UTC), naive: true}, true
	}
	// Any single character separates the date from the time.
	clock, rest, ok := fromISOTimePrefix(rest[1:])
	if !ok || rest != "" {
		return nil, false
	}
	return &pyMoment{t: time.Date(date.year, time.Month(date.month), date.day, clock.hour, clock.minute, clock.second, clock.micro*1000, clock.location), naive: clock.naive, offMicro: clock.offMicro}, true
}

// fromISODate is datetime.date.fromisoformat.
func fromISODate(text string) (pyDate, bool) {
	date, rest, ok := fromISODatePrefix(text)
	if !ok || rest != "" {
		return pyDate{}, false
	}
	return date, true
}

// fromISOTime is datetime.time.fromisoformat (the tzinfo is ignored later).
func fromISOTime(text string) (pyTime, bool) {
	clock, rest, ok := fromISOTimePrefix(text)
	if !ok || rest != "" {
		return pyTime{}, false
	}
	return pyTime{hour: clock.hour, minute: clock.minute, second: clock.second, micro: clock.micro}, true
}

// fromISODatePrefix parses the date part of an ISO string, returning the rest.
func fromISODatePrefix(text string) (pyDate, string, bool) {
	if len(text) < 4 || !asciiDigits(text[:4]) {
		return pyDate{}, "", false
	}
	year := atoiText(text[:4])
	rest := text[4:]
	week := false
	if strings.HasPrefix(rest, "-W") || strings.HasPrefix(rest, "W") {
		week = true
		if strings.HasPrefix(rest, "-W") {
			rest = rest[2:]
		} else {
			rest = rest[1:]
		}
	}
	if week {
		if len(rest) < 2 || !asciiDigits(rest[:2]) {
			return pyDate{}, "", false
		}
		weekNumber := atoiText(rest[:2])
		rest = rest[2:]
		day := 1
		if strings.HasPrefix(rest, "-") {
			if len(rest) < 2 || !asciiDigits(rest[1:2]) {
				return pyDate{}, "", false
			}
			day = atoiText(rest[1:2])
			rest = rest[2:]
		} else if len(rest) > 0 && rest[0] >= '0' && rest[0] <= '9' {
			day = atoiText(rest[:1])
			rest = rest[1:]
		}
		if weekNumber < 1 || weekNumber > 53 || day < 1 || day > 7 {
			return pyDate{}, "", false
		}
		// ISO week-date: week 1 is the week of January 4th.
		january4 := time.Date(year, 1, 4, 0, 0, 0, 0, time.UTC)
		weekday := int(january4.Weekday()+6) % 7 // Monday = 0
		firstMonday := january4.AddDate(0, 0, -weekday)
		day0 := firstMonday.AddDate(0, 0, (weekNumber-1)*7+(day-1))
		return pyDate{year: day0.Year(), month: int(day0.Month()), day: day0.Day()}, rest, true
	}
	if strings.HasPrefix(rest, "-") {
		if len(rest) < 6 || rest[3] != '-' || !asciiDigits(rest[1:3]) || !asciiDigits(rest[4:6]) {
			return pyDate{}, "", false
		}
		date := pyDate{year: year, month: atoiText(rest[1:3]), day: atoiText(rest[4:6])}
		if err := validatePyDate(date); err != nil {
			return pyDate{}, "", false
		}
		return date, rest[6:], true
	}
	if len(rest) < 4 || !asciiDigits(rest[:4]) {
		return pyDate{}, "", false
	}
	date := pyDate{year: year, month: atoiText(rest[:2]), day: atoiText(rest[2:4])}
	if err := validatePyDate(date); err != nil {
		return pyDate{}, "", false
	}
	return date, rest[4:], true
}

// isoClock is the time part of an ISO datetime or time.
type isoClock struct {
	hour, minute, second, micro int
	location                    *time.Location
	naive                       bool
	offMicro                    int
}

// fromISOTimePrefix parses the time part of an ISO string, returning the rest.
func fromISOTimePrefix(text string) (isoClock, string, bool) {
	if len(text) < 2 || !asciiDigits(text[:2]) {
		return isoClock{}, "", false
	}
	clock := isoClock{hour: atoiText(text[:2]), minute: 0, location: time.UTC, naive: true}
	rest := text[2:]
	readClockField := func() (int, bool) {
		if len(rest) < 2 || !asciiDigits(rest[:2]) {
			return 0, false
		}
		value := atoiText(rest[:2])
		rest = rest[2:]
		return value, true
	}
	if strings.HasPrefix(rest, ":") {
		rest = rest[1:]
		minute, ok := readClockField()
		if !ok {
			return isoClock{}, "", false
		}
		clock.minute = minute
		if strings.HasPrefix(rest, ":") {
			rest = rest[1:]
			second, ok := readClockField()
			if !ok {
				return isoClock{}, "", false
			}
			clock.second = second
			if len(rest) > 0 && (rest[0] == '.' || rest[0] == ',') {
				rest = rest[1:]
				digits := 0
				for digits < len(rest) && rest[digits] >= '0' && rest[digits] <= '9' {
					digits++
				}
				if digits == 0 {
					return isoClock{}, "", false
				}
				clock.micro = atoiText(padFraction(rest[:digits]))
				rest = rest[digits:]
			}
		}
	} else if len(rest) >= 2 && asciiDigits(rest[:2]) {
		minute, ok := readClockField()
		if !ok {
			return isoClock{}, "", false
		}
		clock.minute = minute
		if len(rest) >= 2 && asciiDigits(rest[:2]) {
			second, _ := readClockField()
			clock.second = second
		}
		if len(rest) > 0 && (rest[0] == '.' || rest[0] == ',') {
			rest = rest[1:]
			digits := 0
			for digits < len(rest) && rest[digits] >= '0' && rest[digits] <= '9' {
				digits++
			}
			if digits == 0 {
				return isoClock{}, "", false
			}
			clock.micro = atoiText(padFraction(rest[:digits]))
			rest = rest[digits:]
		}
	}
	if clock.hour > 23 || clock.minute > 59 || clock.second > 59 {
		return isoClock{}, "", false
	}
	if rest == "" {
		return clock, "", true
	}
	if rest == "Z" {
		clock.naive = false
		return clock, "", true
	}
	if rest[0] != '+' && rest[0] != '-' {
		return isoClock{}, "", false
	}
	sign := 1
	if rest[0] == '-' {
		sign = -1
	}
	rest = rest[1:]
	readOffset := func() (int, bool) {
		if len(rest) < 2 || !asciiDigits(rest[:2]) {
			return 0, false
		}
		value := atoiText(rest[:2])
		rest = rest[2:]
		return value, true
	}
	hours, ok := readOffset()
	if !ok {
		return isoClock{}, "", false
	}
	minutes, seconds, micro := 0, 0, 0
	if strings.HasPrefix(rest, ":") {
		rest = rest[1:]
		if minutes, ok = readOffset(); !ok {
			return isoClock{}, "", false
		}
		if strings.HasPrefix(rest, ":") {
			rest = rest[1:]
			if seconds, ok = readOffset(); !ok {
				return isoClock{}, "", false
			}
			if len(rest) > 0 && rest[0] == '.' {
				rest = rest[1:]
				digits := 0
				for digits < len(rest) && rest[digits] >= '0' && rest[digits] <= '9' {
					digits++
				}
				if digits == 0 {
					return isoClock{}, "", false
				}
				micro = atoiText(padFraction(rest[:digits]))
				rest = rest[digits:]
			}
		}
	} else if len(rest) >= 2 && asciiDigits(rest[:2]) {
		if minutes, ok = readOffset(); !ok {
			return isoClock{}, "", false
		}
		if len(rest) >= 2 && asciiDigits(rest[:2]) {
			seconds, _ = readOffset()
		}
	}
	if rest != "" || minutes > 59 || seconds > 59 || hours > 23 {
		return isoClock{}, "", false
	}
	offsetSeconds := sign * (hours*3600 + minutes*60 + seconds)
	clock.location = time.FixedZone("", offsetSeconds)
	clock.naive = false
	clock.offMicro = sign * micro
	return clock, "", true
}

func padFraction(digits string) string {
	if len(digits) >= 6 {
		return digits[:6]
	}
	return digits + strings.Repeat("0", 6-len(digits))
}

// fallbackMatch matches pattern against text with Unicode decimal digits
// mapped to ASCII; Python's `$` also matches before a single final newline.
func fallbackMatch(pattern *regexp.Regexp, text string) []string {
	normalized := normalizeDigits(text)
	if match := pattern.FindStringSubmatch(normalized); match != nil {
		return match
	}
	if strings.HasSuffix(normalized, "\n") {
		return pattern.FindStringSubmatch(normalized[:len(normalized)-1])
	}
	return nil
}

// normalizeDigits maps every Unicode decimal digit to its ASCII digit.
func normalizeDigits(text string) string {
	ascii := true
	for _, r := range text {
		if r > '9' {
			ascii = false
			break
		}
	}
	if ascii {
		return text
	}
	return strings.Map(func(r rune) rune {
		if digit, ok := pyDecimalDigit(r); ok {
			return rune('0' + digit)
		}
		return r
	}, text)
}

func asciiDigits(text string) bool {
	for i := 0; i < len(text); i++ {
		if text[i] < '0' || text[i] > '9' {
			return false
		}
	}
	return true
}

func atoiText(text string) int {
	n, _ := strconv.Atoi(text)
	return n
}

// pythonNaive is str() of a naive datetime with t's wall time.
func pythonNaive(t time.Time) string {
	text := fmt.Sprintf("%04d-%02d-%02d %02d:%02d:%02d", t.Year(), int(t.Month()), t.Day(), t.Hour(), t.Minute(), t.Second())
	if micro := t.Nanosecond() / 1000; micro != 0 {
		text += fmt.Sprintf(".%06d", micro)
	}
	return text
}

// pythonDateRepr is repr(datetime.date).
func pythonDateRepr(text string) string {
	year, _ := strconv.Atoi(text[:4])
	month, _ := strconv.Atoi(text[5:7])
	day, _ := strconv.Atoi(text[8:10])
	return fmt.Sprintf("datetime.date(%d, %d, %d)", year, month, day)
}

// pythonDatetimeRepr is repr(datetime.datetime).
func pythonDatetimeRepr(m pyMoment) string {
	t := m.t
	text := fmt.Sprintf("datetime.datetime(%d, %d, %d, %d, %d", t.Year(), int(t.Month()), t.Day(), t.Hour(), t.Minute())
	if t.Second() != 0 || t.Nanosecond() != 0 {
		text += fmt.Sprintf(", %d", t.Second())
		if micro := t.Nanosecond() / 1000; micro != 0 {
			text += fmt.Sprintf(", %d", micro)
		}
	}
	if !m.naive {
		_, offset := t.Zone()
		if offset == 0 && m.offMicro == 0 {
			text += ", tzinfo=datetime.timezone.utc"
		} else {
			text += ", tzinfo=" + pythonTimezoneRepr(offset, m.offMicro)
		}
	}
	return text + ")"
}

// pythonTimezoneRepr is repr(datetime.timezone(timedelta(...))).
func pythonTimezoneRepr(offsetSeconds, offMicro int) string {
	nanos := int64(offsetSeconds)*int64(time.Second) + int64(offMicro)*int64(time.Microsecond)
	return "datetime.timezone(datetime.timedelta(" + pythonTimedeltaTerms(nanos, 0) + "))"
}

func pad2(n int) string {
	if n < 10 {
		return "0" + strconv.Itoa(n)
	}
	return strconv.Itoa(n)
}
