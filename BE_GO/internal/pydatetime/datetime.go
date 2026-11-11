// Package pydatetime ports django.utils.dateparse.parse_datetime: CPython
// 3.13's datetime.fromisoformat, then Django's regular expression.
package pydatetime

import (
	"errors"
	"fmt"
	"regexp"
	"time"
	"unicode"
)

// ErrValue is the ValueError parse_datetime raises for text that is shaped
// like a datetime but is not one.
var ErrValue = errors.New("pydatetime: ValueError")

// Moment is a datetime.datetime with an optional fixed-offset tzinfo.
type Moment struct {
	Year, Month, Day, Hour, Minute, Second, Micro int
	Aware                                         bool
	// Offset is the UTC offset in microseconds (Aware only).
	Offset int64
	// DjangoZone marks a tzinfo from django.utils.timezone.get_fixed_timezone
	// (named "+HHMM") rather than datetime.timezone's own naming.
	DjangoZone bool
}

// Wall is the wall clock as a UTC time.Time.
func (m Moment) Wall() time.Time {
	return time.Date(m.Year, time.Month(m.Month), m.Day, m.Hour, m.Minute, m.Second, m.Micro*1000, time.UTC)
}

// ZoneName is tzinfo.tzname(): "UTC" at offset zero for the ISO reader, the
// "UTC+HH:MM[:SS[.ffffff]]" name of datetime.timezone, or Django's "+HHMM".
func (m Moment) ZoneName() string {
	if m.DjangoZone {
		minutes := m.Offset / 60000000
		sign := "+"
		if minutes < 0 {
			sign, minutes = "-", -minutes
		}
		return fmt.Sprintf("%s%02d%02d", sign, minutes/60, minutes%60)
	}
	if m.Offset == 0 {
		return "UTC"
	}
	offset, sign := m.Offset, "+"
	if offset < 0 {
		sign, offset = "-", -offset
	}
	seconds, micro := offset/1000000, offset%1000000
	name := fmt.Sprintf("UTC%s%02d:%02d", sign, seconds/3600, seconds/60%60)
	if seconds%60 != 0 || micro != 0 {
		name += fmt.Sprintf(":%02d", seconds%60)
	}
	if micro != 0 {
		name += fmt.Sprintf(".%06d", micro)
	}
	return name
}

// UTCInstant is the instant the moment denotes: naive values are taken as UTC
// by the callers that need one (calendar_datetime_in_rome); ok is false when
// subtracting the offset leaves datetime's range (OverflowError).
func (m Moment) UTCInstant() (time.Time, bool) {
	wall := m.Wall()
	if m.Aware {
		wall = wall.Add(-time.Duration(m.Offset) * time.Microsecond)
	}
	if wall.Year() < 1 || wall.Year() > 9999 {
		return wall, false
	}
	return wall, true
}

// FromISOFormat is datetime.datetime.fromisoformat(text); ok is false for a
// ValueError.
func FromISOFormat(text string) (Moment, bool) {
	parts, ok := parseISO(text)
	if !ok {
		return Moment{}, false
	}
	return Moment{Year: parts.year, Month: parts.month, Day: parts.day, Hour: parts.hour, Minute: parts.minute,
		Second: parts.second, Micro: parts.micro, Aware: parts.hasZone, Offset: parts.offset}, true
}

const digit = `\p{Nd}`

var datetimePattern = regexp.MustCompile(`^(` + digit + `{4})-(` + digit + `{1,2})-(` + digit + `{1,2})[T ](` + digit + `{1,2}):(` + digit +
	`{1,2})(?::(` + digit + `{1,2})(?:[.,](` + digit + `{1,6})` + digit + `{0,6})?)?[\s\x{0b}\p{Z}\x{85}\x{1c}-\x{1f}]*(Z|[+-]` + digit + `{2}(?::?` + digit + `{2})?)?\n?$`)

// ParseDatetime is django.utils.dateparse.parse_datetime for a str: a Moment,
// nil when the text is not shaped like a datetime (None), or ErrValue.
func ParseDatetime(text string) (*Moment, error) {
	if moment, ok := FromISOFormat(text); ok {
		return &moment, nil
	}
	match := datetimePattern.FindStringSubmatch(text)
	if match == nil {
		return nil, nil
	}
	number := func(index int) int {
		value := 0
		for _, r := range match[index] {
			d, _ := DecimalDigit(r)
			value = value*10 + d
		}
		return value
	}
	moment := Moment{Year: number(1), Month: number(2), Day: number(3), Hour: number(4), Minute: number(5)}
	if match[6] != "" {
		moment.Second = number(6)
	}
	if match[7] != "" {
		moment.Micro = number(7)
		for i := len([]rune(match[7])); i < 6; i++ {
			moment.Micro *= 10
		}
	}
	if moment.Year < 1 || moment.Month < 1 || moment.Month > 12 || moment.Day < 1 || moment.Hour > 23 || moment.Minute > 59 ||
		moment.Second > 59 || moment.Day > daysIn(moment.Year, moment.Month) {
		return nil, ErrValue
	}
	if zone := match[8]; zone != "" {
		moment.Aware = true
		if zone != "Z" {
			runes := []rune(zone)
			digits := func(from, to int) int {
				value := 0
				for _, r := range runes[from:to] {
					d, _ := DecimalDigit(r)
					value = value*10 + d
				}
				return value
			}
			minutes := 0
			if len(runes) > 3 {
				minutes = digits(len(runes)-2, len(runes))
			}
			offset := 60*digits(1, 3) + minutes
			if runes[0] == '-' {
				offset = -offset
			}
			if offset <= -24*60 || offset >= 24*60 {
				return nil, ErrValue // timezone(): offset must be strictly within a day
			}
			moment.Offset = int64(offset) * 60000000
			moment.DjangoZone = true
		}
	}
	return &moment, nil
}

// decimalDigit is the value of a Unicode Nd character (series start at a zero).
func DecimalDigit(r rune) (int, bool) {
	if r >= '0' && r <= '9' {
		return int(r - '0'), true
	}
	if r < 0x80 || !unicode.Is(unicode.Nd, r) {
		return 0, false
	}
	for _, rng := range unicode.Nd.R16 {
		if r >= rune(rng.Lo) && r <= rune(rng.Hi) {
			return int(r-rune(rng.Lo)) % 10, true
		}
	}
	for _, rng := range unicode.Nd.R32 {
		if r >= rune(rng.Lo) && r <= rune(rng.Hi) {
			return int(r-rune(rng.Lo)) % 10, true
		}
	}
	return 0, false
}
