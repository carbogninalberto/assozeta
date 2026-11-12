package service

import (
	"math"
	"math/big"
	"strings"
	"time"
)

// automationWorkflowISOTime is a naive datetime.fromisoformat() result.
type automationWorkflowISOTime struct {
	year, month, day, hour, minute, second, microsecond int
}

func automationWorkflowDigits(text []rune) (int, bool) {
	value := 0
	for _, r := range text {
		if r < '0' || r > '9' {
			return 0, false
		}
		value = value*10 + int(r-'0')
	}
	return value, len(text) > 0
}

// automationWorkflowFromISO is Python 3.13's datetime.fromisoformat(text)
// for the strings that give a naive datetime; ok is false when Python raises
// ValueError or the result carries a UTC offset (pytz's localize() then
// raises): YYYY-MM-DD, YYYYMMDD, YYYY-Www[-D] or YYYYWww[D], then any one
// character and HH[[:]MM[[:]SS[(.|,)f...]]].
func automationWorkflowFromISO(value string) (result automationWorkflowISOTime, ok bool) {
	text := []rune(value)
	if len(text) < 7 {
		return result, false
	}
	separator := 7
	if len(text) > 7 {
		switch {
		case text[4] == '-' && text[5] == 'W':
			separator = 8
			if len(text) > 8 && text[8] == '-' {
				if len(text) == 9 {
					return result, false
				}
				if !(len(text) > 10 && text[10] >= '0' && text[10] <= '9') {
					separator = 10
				}
			}
		case text[4] == '-':
			separator = 10
		case text[4] == 'W':
			index := 7
			for index < len(text) && text[index] >= '0' && text[index] <= '9' {
				index++
			}
			switch {
			case index < 9:
				separator = index
			case index%2 == 0:
				separator = 7
			default:
				separator = 8
			}
		default:
			separator = 8
		}
	}
	date := text[:min(separator, len(text))]
	var clock []rune
	if separator+1 < len(text) {
		clock = text[separator+1:]
	}
	if len(date) != 7 && len(date) != 8 && len(date) != 10 {
		return result, false
	}
	year, valid := automationWorkflowDigits(date[:4])
	if !valid {
		return result, false
	}
	dashes := date[4] == '-'
	position := 4
	if dashes {
		position++
	}
	part := func(length int) (int, bool) {
		if position+length > len(date) {
			return 0, false
		}
		number, valid := automationWorkflowDigits(date[position : position+length])
		position += length
		return number, valid
	}
	dash := func() bool {
		// (next == '-') must equal the date's use of dashes.
		if (position < len(date) && date[position] == '-') != dashes {
			return false
		}
		if dashes {
			position++
		}
		return true
	}
	if position < len(date) && date[position] == 'W' {
		position++
		week, valid := part(2)
		if !valid {
			return result, false
		}
		weekday := 1
		if len(date) > position {
			if !dash() {
				return result, false
			}
			if weekday, valid = part(1); !valid {
				return result, false
			}
		}
		if position != len(date) || year < 1 || week < 1 || weekday < 1 || weekday > 7 {
			return result, false
		}
		first := time.Date(year, 1, 1, 0, 0, 0, 0, time.UTC)
		leap := year%4 == 0 && (year%100 != 0 || year%400 == 0)
		if week > 53 || week == 53 && !(first.Weekday() == time.Thursday || first.Weekday() == time.Wednesday && leap) {
			return result, false
		}
		monday := (int(first.Weekday()) + 6) % 7
		start := first.AddDate(0, 0, -monday)
		if monday > 3 {
			start = start.AddDate(0, 0, 7)
		}
		day := start.AddDate(0, 0, (week-1)*7+weekday-1)
		result.year, result.month, result.day = day.Year(), int(day.Month()), day.Day()
	} else {
		var validMonth, validDay bool
		result.year = year
		if result.month, validMonth = part(2); !validMonth || !dash() {
			return result, false
		}
		if result.day, validDay = part(2); !validDay || position != len(date) {
			return result, false
		}
	}
	if result.year < 1 || result.year > 9999 || result.month < 1 || result.month > 12 || result.day < 1 ||
		result.day > time.Date(result.year, time.Month(result.month)+1, 0, 0, 0, 0, 0, time.UTC).Day() {
		return result, false
	}
	if len(clock) == 0 {
		// A separator with nothing after it is rejected.
		return result, separator >= len(text)
	}
	if len(clock) < 2 || strings.ContainsAny(string(clock), "-+Z") {
		// A UTC offset (or text Python rejects).
		return result, false
	}
	// CPython's parse_hh_mm_ss_ff: two digits per component; the character after each one is consumed
	// (':' with a separator, '.' or ',' starts the fraction, anything else is the next digit).
	fields := [3]*int{&result.hour, &result.minute, &result.second}
	position, colons, ended := 0, false, false
	for index := 0; index < 3 && !ended; index++ {
		if position+2 > len(clock) {
			return result, false
		}
		number, valid := automationWorkflowDigits(clock[position : position+2])
		if !valid {
			return result, false
		}
		*fields[index] = number
		position += 2
		if position >= len(clock) {
			ended = true
			break
		}
		next := clock[position]
		position++
		if index == 0 {
			colons = next == ':'
		}
		switch {
		case position >= len(clock):
			return result, false
		case colons && next == ':':
		case next == '.' || next == ',':
			index = 3
		case !colons:
			position--
		default:
			return result, false
		}
	}
	if !ended {
		fraction := clock[position:]
		take := min(len(fraction), 6)
		if _, valid := automationWorkflowDigits(fraction[:take]); !valid {
			return result, false
		}
		for _, r := range fraction[take:] {
			if r < '0' || r > '9' {
				return result, false
			}
		}
		padded := append([]rune{}, fraction[:take]...)
		for len(padded) < 6 {
			padded = append(padded, '0')
		}
		result.microsecond, _ = automationWorkflowDigits(padded)
	}
	if result.hour > 23 || result.minute > 59 || result.second > 59 {
		return result, false
	}
	return result, true
}

// automationWorkflowETA is Celery's eta for trigger()'s countdown:
// now + timedelta(seconds=(at - now).total_seconds()), in UTC. The float of
// total_seconds() and timedelta's rounding of it to microseconds are
// followed, so a time centuries ahead gives the same microsecond.
func automationWorkflowETA(now, at time.Time) time.Time {
	now = now.Truncate(time.Microsecond)
	delta := (at.Unix()-now.Unix())*1_000_000 + int64(at.Nanosecond()-now.Nanosecond())/1000
	seconds, _ := new(big.Rat).SetFrac64(delta, 1_000_000).Float64()
	whole, fraction := math.Modf(seconds)
	micros, left := math.Modf(fraction * 1e6)
	total := int64(whole)*1_000_000 + int64(micros)
	if left > 0.5 || left == 0.5 && total%2 != 0 {
		total++
	}
	return time.UnixMicro(now.UnixMicro() + total).UTC()
}
