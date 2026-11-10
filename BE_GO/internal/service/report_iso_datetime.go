package service

import (
	"fmt"
	"strings"
	"time"
)

// mcpISODateTime is exporters.format_value's date-time branch for text:
// datetime.fromisoformat(text.replace('Z', '+00:00')) (CPython 3.13, the C
// parser working on the UTF-8 bytes) rendered '%d/%m/%Y %H:%M'. ok is false
// when fromisoformat raises ValueError (format_value then keeps the text).
func mcpISODateTime(text string) (string, bool) {
	s := strings.ReplaceAll(text, "Z", "+00:00")
	n := len(s)
	at := func(i int) byte { // the C string: a NUL after the last byte
		if i >= 0 && i < n {
			return s[i]
		}
		return 0
	}
	digit := func(c byte) bool { return c >= '0' && c <= '9' }
	digits := func(p *int, count int) (int, bool) {
		value := 0
		for i := 0; i < count; i++ {
			c := at(*p)
			*p++
			if !digit(c) {
				return 0, false
			}
			value = value*10 + int(c-'0')
		}
		return value, true
	}
	if n < 8 {
		return "", false
	}
	// _find_isoformat_datetime_separator
	separator := 10
	if at(4) == '-' {
		if at(5) == 'W' {
			if n > 8 && at(8) == '-' {
				if n == 9 {
					return "", false
				}
				if n > 10 && digit(at(10)) {
					separator = 8
				}
			} else {
				separator = 8
			}
		}
	} else if at(4) == 'W' {
		index := 7
		for index < n && digit(at(index)) {
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
	} else {
		separator = 8
	}
	// parse_isoformat_date
	p := 0
	year, ok := digits(&p, 4)
	if !ok {
		return "", false
	}
	dashed := at(p) == '-'
	if dashed {
		p++
	}
	var month, day int
	if at(p) == 'W' {
		p++
		week, ok := digits(&p, 2)
		if !ok {
			return "", false
		}
		weekday := 1
		if p < separator {
			if dashed {
				p++
				if at(p-1) != '-' {
					return "", false
				}
			}
			if weekday, ok = digits(&p, 1); !ok {
				return "", false
			}
		}
		// iso_to_ymd
		if year < 1 || year > 9999 || weekday < 1 || weekday > 7 {
			return "", false
		}
		first := time.Date(year, 1, 1, 0, 0, 0, 0, time.UTC)
		monday0 := (int(first.Weekday()) + 6) % 7 // Monday is 0
		if week <= 0 || week >= 53 {
			leap := year%4 == 0 && (year%100 != 0 || year%400 == 0)
			if week != 53 || !(monday0 == 3 || (monday0 == 2 && leap)) {
				return "", false
			}
		}
		// iso_week1_monday: the Monday of the week holding the first Thursday.
		weekOne := -monday0
		if monday0 > 3 {
			weekOne += 7
		}
		date := first.AddDate(0, 0, weekOne+(week-1)*7+weekday-1)
		year, month, day = date.Year(), int(date.Month()), date.Day()
	} else {
		if month, ok = digits(&p, 2); !ok {
			return "", false
		}
		if dashed {
			p++
			if at(p-1) != '-' {
				return "", false
			}
		}
		if day, ok = digits(&p, 2); !ok {
			return "", false
		}
	}
	var hour, minute, second int
	if n > separator {
		// One UTF-8 character separates the date from the time.
		start := separator + 1
		if lead := at(separator); lead&0x80 != 0 {
			switch lead & 0xf0 {
			case 0xe0:
				start = separator + 3
			case 0xf0:
				start = separator + 4
			default:
				start = separator + 2
			}
		}
		// parse_hh_mm_ss_ff: 0 at the end of the string, 1 with text left, negative when malformed.
		clock := func(from, end int, values *[3]int) (int, int) {
			p := from
			separated := true
			fraction := false
			for i := 0; i < 3 && !fraction; i++ {
				value, ok := digits(&p, 2)
				if !ok {
					return -3, 0
				}
				values[i] = value
				c := at(p)
				p++
				if i == 0 {
					separated = c == ':'
				}
				switch {
				case c == '.' || c == ',':
					if p >= end {
						return -3, 0
					}
					fraction = true
				case p >= end:
					if c != 0 {
						return 1, 0
					}
					return 0, 0
				case separated && c == ':':
				case !separated:
					p--
				default:
					return -4, 0
				}
			}
			count := min(end-p, 6)
			micro, ok := digits(&p, count)
			if !ok {
				return -3, 0
			}
			for i := count; i < 6; i++ {
				micro *= 10
			}
			for digit(at(p)) {
				p++
			}
			if at(p) != 0 {
				return 1, micro
			}
			return 0, micro
		}
		// parse_isoformat_time
		zone := start
		for {
			if c := at(zone); c == 'Z' || c == '+' || c == '-' {
				break
			}
			zone++
			if zone >= n {
				break
			}
		}
		var values [3]int
		state, _ := clock(start, zone, &values)
		if state < 0 || (zone >= n && state == 1) {
			return "", false
		}
		hour, minute, second = values[0], values[1], values[2]
		if zone < n {
			if at(zone) == 'Z' {
				if at(zone+1) != 0 {
					return "", false
				}
			} else {
				var offset [3]int
				state, micro := clock(zone+1, n, &offset)
				if state != 0 {
					return "", false
				}
				// timezone(): strictly between -24 and 24 hours.
				seconds := offset[0]*3600 + offset[1]*60 + offset[2]
				if seconds > 86400 || (seconds == 86400 && micro >= 0) {
					return "", false
				}
			}
		}
	}
	// new_datetime: the range checks of the fields.
	if year < 1 || year > 9999 || month < 1 || month > 12 || day < 1 || hour > 23 || minute > 59 || second > 59 ||
		day > time.Date(year, time.Month(month)+1, 0, 0, 0, 0, 0, time.UTC).Day() {
		return "", false
	}
	return fmt.Sprintf("%02d/%02d/%04d %02d:%02d", day, month, year, hour, minute), true
}
