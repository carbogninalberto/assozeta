package pydatetime

import (
	"strconv"
	"time"
)

// isoParts is a datetime.fromisoformat result. offset is the UTC offset in
// microseconds, set only when hasZone.
type isoParts struct {
	year, month, day, hour, minute, second, micro int
	hasZone                                       bool
	offset                                        int64
}

func allDigits(text string) bool {
	if text == "" {
		return false
	}
	for i := 0; i < len(text); i++ {
		if text[i] < '0' || text[i] > '9' {
			return false
		}
	}
	return true
}

// isoComponent is _read_isoformat_component: exactly n ASCII digits.
func isoComponent(text string, n int) (int, bool) {
	if len(text) != n || !allDigits(text) {
		return 0, false
	}
	value, _ := strconv.Atoi(text)
	return value, true
}

func isoSlice(text string, from, to int) string {
	if from > len(text) {
		from = len(text)
	}
	if to > len(text) {
		to = len(text)
	}
	return text[from:to]
}

// isoSeparator is _find_isoformat_datetime_separator for len(text) >= 7.
func isoSeparator(text string) (int, bool) {
	size := len(text)
	if size == 7 {
		return 7, true
	}
	switch {
	case text[4] == '-' && text[5] == 'W':
		if size > 8 && text[8] == '-' {
			if size == 9 {
				return 0, false
			}
			if size > 10 && text[10] >= '0' && text[10] <= '9' {
				return 8, true
			}
			return 10, true
		}
		return 8, true
	case text[4] == '-':
		return 10, true
	case text[4] == 'W':
		index := 7
		for index < size && text[index] >= '0' && text[index] <= '9' {
			index++
		}
		if index < 9 {
			return index, true
		}
		if index%2 == 0 {
			return 7, true
		}
		return 8, true
	}
	return 8, true
}

func isoLeap(year int) bool { return year%4 == 0 && (year%100 != 0 || year%400 == 0) }

func parseISO(text string) (isoParts, bool) {
	var out isoParts
	if len(text) < 7 {
		return out, false
	}
	split, ok := isoSeparator(text)
	if !ok {
		return out, false
	}
	if split > len(text) {
		return out, false
	}
	// A multi-byte separator is skipped by its UTF-8 length (CPython reads it
	// from the encoded string); other non-ASCII bytes fail the digit checks.
	skip := 1
	if split < len(text) {
		switch lead := text[split]; {
		case lead&0x80 == 0:
		case lead&0xf0 == 0xe0:
			skip = 3
		case lead&0xf0 == 0xf0:
			skip = 4
		default:
			skip = 2
		}
	}
	date, clock := text[:split], isoSlice(text, split+skip, len(text))
	dateOnly := split >= len(text)
	if len(date) != 7 && len(date) != 8 && len(date) != 10 {
		return out, false
	}
	out, ok = parseISODate(date)
	if !ok {
		return out, false
	}
	if dateOnly {
		return out, true
	}
	return parseISOTime(clock, out)
}

// isoByte reads text[i], with the buffer's terminating NUL past the end.
func isoByte(text string, i int) byte {
	if i < len(text) {
		return text[i]
	}
	return 0
}

// isoDigits is parse_digits: exactly n ASCII digits at pos.
func isoDigits(text string, pos, n int) (int, int, bool) {
	value := 0
	for i := 0; i < n; i++ {
		c := isoByte(text, pos+i)
		if c < '0' || c > '9' {
			return 0, pos, false
		}
		value = value*10 + int(c-'0')
	}
	return value, pos + n, true
}

// isoClock is the C parse_hh_mm_ss_ff over text[from:end] (the buffer
// continues past end, which the C reader relies on). more is its return code 1:
// something other than the NUL follows the parsed part.
func isoClock(text string, from, end int) (parts [4]int, more, ok bool) {
	pos, hasSep := from, true
	for i := 0; i < 3; i++ {
		value, next, valid := isoDigits(text, pos, 2)
		if !valid {
			return parts, false, false
		}
		parts[i], pos = value, next
		c := isoByte(text, pos)
		pos++
		if i == 0 {
			hasSep = c == ':'
		}
		switch {
		case c == '.' || c == ',':
			i = 3
		case pos >= end:
			return parts, c != 0, true
		case hasSep && c == ':':
			continue
		case !hasSep:
			pos--
		default:
			return parts, false, false
		}
	}
	remains := end - pos
	take := remains
	if take >= 6 {
		take = 6
	}
	if take < 1 {
		return parts, false, false
	}
	value, next, valid := isoDigits(text, pos, take)
	if !valid {
		return parts, false, false
	}
	for i := take; i < 6; i++ {
		value *= 10
	}
	parts[3], pos = value, next
	for isoByte(text, pos) >= '0' && isoByte(text, pos) <= '9' {
		pos++
	}
	return parts, isoByte(text, pos) != 0, true
}

// parseISOTime is the C parse_isoformat_time plus the datetime range checks.
func parseISOTime(text string, date isoParts) (isoParts, bool) {
	if len(text) < 2 {
		return date, false
	}
	zone := 0
	for zone < len(text) && text[zone] != 'Z' && text[zone] != '+' && text[zone] != '-' {
		zone++
	}
	parts, more, ok := isoClock(text, 0, zone)
	if !ok {
		return date, false
	}
	if zone == len(text) {
		if more {
			return date, false
		}
	} else if text[zone] == 'Z' {
		if zone+1 != len(text) {
			return date, false
		}
		date.hasZone = true
	} else {
		tz, tzMore, tzOK := isoClock(text, zone+1, len(text))
		if !tzOK || tzMore {
			return date, false
		}
		micro := int64(((tz[0]*60+tz[1])*60+tz[2]))*1000000 + int64(tz[3])
		if micro >= 24*3600*1000000 {
			return date, false
		}
		if text[zone] == '-' {
			micro = -micro
		}
		date.hasZone, date.offset = true, micro
	}
	if parts[0] > 23 || parts[1] > 59 || parts[2] > 59 {
		return date, false
	}
	date.hour, date.minute, date.second, date.micro = parts[0], parts[1], parts[2], parts[3]
	return date, true
}

// parseISODate is the C parse_isoformat_date over a 7, 8 or 10 byte
// ASCII date: YYYY-MM-DD, YYYYMMDD, YYYY-Www[-D] and YYYYWww[D].
func parseISODate(date string) (isoParts, bool) {
	var out isoParts
	if len(date) != 7 && len(date) != 8 && len(date) != 10 {
		return out, false
	}
	year, ok := isoComponent(date[:4], 4)
	if !ok {
		return out, false
	}
	hasSep := date[4] == '-'
	pos := 4
	if hasSep {
		pos = 5
	}
	if isoSlice(date, pos, pos+1) == "W" {
		pos++
		week, ok := isoComponent(isoSlice(date, pos, pos+2), 2)
		if !ok {
			return out, false
		}
		pos += 2
		weekday := 1
		if len(date) > pos {
			if (date[pos] == '-') != hasSep {
				return out, false
			}
			if hasSep {
				pos++
			}
			if weekday, ok = isoComponent(isoSlice(date, pos, pos+1), 1); !ok {
				return out, false
			}
		}
		if year < 1 || year > 9999 || weekday < 1 || weekday > 7 {
			return out, false
		}
		if week < 1 || week > 53 {
			return out, false
		}
		if week == 53 {
			first := (int(time.Date(year, 1, 1, 0, 0, 0, 0, time.UTC).Weekday()) + 6) % 7
			if !(first == 3 || (first == 2 && isoLeap(year))) {
				return out, false
			}
		}
		fourth := time.Date(year, 1, 4, 0, 0, 0, 0, time.UTC)
		monday := fourth.AddDate(0, 0, -((int(fourth.Weekday()) + 6) % 7))
		day := monday.AddDate(0, 0, (week-1)*7+weekday-1)
		if day.Year() < 1 || day.Year() > 9999 {
			return out, false
		}
		out.year, out.month, out.day = day.Year(), int(day.Month()), day.Day()
	} else {
		month, ok := isoComponent(isoSlice(date, pos, pos+2), 2)
		if !ok {
			return out, false
		}
		pos += 2
		if (isoSlice(date, pos, pos+1) == "-") != hasSep {
			return out, false
		}
		if hasSep {
			pos++
		}
		day, ok := isoComponent(isoSlice(date, pos, pos+2), 2)
		if !ok {
			return out, false
		}
		if year < 1 || month < 1 || month > 12 || day < 1 || day > daysIn(year, month) {
			return out, false
		}
		out.year, out.month, out.day = year, month, day
	}
	return out, true
}

func daysIn(year, month int) int {
	return time.Date(year, time.Month(month)+1, 0, 0, 0, 0, 0, time.UTC).Day()
}
