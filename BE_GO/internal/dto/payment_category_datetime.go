package dto

import (
	"errors"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

var errPaymentCategoryDateTimeOverflow = errors.New("payment category datetime UTC conversion overflow")

// parsePaymentCategoryDjangoDateTime follows the two parsers used by the pinned
// Django dateparse.parse_datetime: Python 3.13's ISO parser, then Django's
// narrower variable-width calendar-date fallback. Naive values use the
// application's UTC timezone. Do not trim input: whitespace acceptance differs
// between these two grammars.
func parsePaymentCategoryDjangoDateTime(value string) (time.Time, error) {
	if parsed, ok := paymentCategoryISODateTime(value); ok {
		if parsed.Year() < 1 || parsed.Year() > 9999 {
			return time.Time{}, errPaymentCategoryDateTimeOverflow
		}
		return parsed, nil
	}
	if parsed, ok := paymentCategoryFallbackDateTime(value); ok {
		if parsed.Year() < 1 || parsed.Year() > 9999 {
			return time.Time{}, errPaymentCategoryDateTimeOverflow
		}
		return parsed, nil
	}
	return time.Time{}, domain.ErrPaymentCategoryInvalidID
}

func paymentCategoryISODateTime(value string) (time.Time, bool) {
	runes := paymentCategoryDateTimeRunes(value)
	separator := paymentCategoryISOSeparator(runes)
	if separator < 0 || separator > len(runes) {
		return time.Time{}, false
	}
	date, ok := paymentCategoryISODate(string(runes[:separator]))
	if !ok {
		return time.Time{}, false
	}
	if separator == len(runes) {
		return date, true
	}
	clock := string(runes[separator+1:]) // Python permits any single separator.
	zone := strings.IndexAny(clock, "+-Z")
	clockPart := clock
	if zone >= 0 {
		clockPart = clock[:zone]
	}
	hour, minute, second, micros, ok := paymentCategoryISOClock(clockPart, zone >= 0)
	if !ok || hour > 23 || minute > 59 || second > 59 {
		return time.Time{}, false
	}
	var offset time.Duration
	if zone >= 0 {
		if clock[zone] == 'Z' {
			if zone != len(clock)-1 {
				return time.Time{}, false
			}
		} else {
			h, m, s, us, valid := paymentCategoryISOClock(clock[zone+1:], false)
			if !valid {
				return time.Time{}, false
			}
			// Python constructs a timedelta: offset minutes/seconds can exceed
			// 59, provided the normalized total remains strictly below 24h.
			offset = time.Duration(h)*time.Hour + time.Duration(m)*time.Minute + time.Duration(s)*time.Second + time.Duration(us)*time.Microsecond
			if offset >= 24*time.Hour {
				return time.Time{}, false
			}
			if clock[zone] == '-' {
				offset = -offset
			}
		}
	}
	return paymentCategoryUTCDateTime(date, hour, minute, second, micros, offset)
}

// The category JSON parser preserves escaped lone UTF-16 surrogates as their
// three-byte code units. Python permits one of these at the date/time separator
// (but not inside date digits), so keep it one character during splitting.
func paymentCategoryDateTimeRunes(value string) []rune {
	result := make([]rune, 0, len(value))
	for len(value) > 0 {
		if len(value) >= 3 && value[0] == 0xed && value[1] >= 0xa0 && value[1] <= 0xbf && value[2] >= 0x80 && value[2] <= 0xbf {
			result = append(result, rune(value[0]&0x0f)<<12|rune(value[1]&0x3f)<<6|rune(value[2]&0x3f))
			value = value[3:]
			continue
		}
		r, size := utf8.DecodeRuneInString(value)
		result = append(result, r)
		value = value[size:]
	}
	return result
}

// This disambiguation mirrors Python's _find_isoformat_datetime_separator.
// Week dates can omit their weekday, and the separator itself can be a digit.
func paymentCategoryISOSeparator(value []rune) int {
	n := len(value)
	if n < 7 {
		return -1
	}
	if n == 7 {
		return 7
	}
	if value[4] == '-' {
		if value[5] != 'W' {
			return 10
		}
		if n > 8 && value[8] == '-' {
			if n == 9 {
				return -1
			}
			if n > 10 && value[10] >= '0' && value[10] <= '9' {
				return 8
			}
			return 10
		}
		return 8
	}
	if value[4] == 'W' {
		i := 7
		for i < n && value[i] >= '0' && value[i] <= '9' {
			i++
		}
		if i < 9 {
			return i
		}
		if i%2 == 0 {
			return 7
		}
		return 8
	}
	return 8
}

func paymentCategoryISODate(value string) (time.Time, bool) {
	if len(value) != 7 && len(value) != 8 && len(value) != 10 {
		return time.Time{}, false
	}
	year, ok := paymentCategoryDigits(value[:4])
	if !ok || year < 1 {
		return time.Time{}, false
	}
	extended := value[4] == '-'
	pos := 4
	if extended {
		pos++
	}
	if value[pos] == 'W' {
		pos++
		if pos+2 > len(value) {
			return time.Time{}, false
		}
		week, valid := paymentCategoryDigits(value[pos : pos+2])
		pos += 2
		day := 1
		if pos < len(value) {
			if extended {
				if value[pos] != '-' {
					return time.Time{}, false
				}
				pos++
			}
			if pos+1 != len(value) {
				return time.Time{}, false
			}
			day, ok = paymentCategoryDigits(value[pos:])
			valid = valid && ok
		}
		if !valid || week < 1 || week > 53 || day < 1 || day > 7 {
			return time.Time{}, false
		}
		jan4 := time.Date(year, 1, 4, 0, 0, 0, 0, time.UTC)
		monday := jan4.AddDate(0, 0, -(int(jan4.Weekday())+6)%7)
		date := monday.AddDate(0, 0, (week-1)*7+day-1)
		isoYear, isoWeek := date.ISOWeek()
		return date, isoYear == year && isoWeek == week && date.Year() <= 9999
	}
	if pos+2 > len(value) {
		return time.Time{}, false
	}
	month, valid := paymentCategoryDigits(value[pos : pos+2])
	pos += 2
	if extended {
		if pos >= len(value) || value[pos] != '-' {
			return time.Time{}, false
		}
		pos++
	}
	if pos+2 != len(value) {
		return time.Time{}, false
	}
	day, ok := paymentCategoryDigits(value[pos:])
	if !ok || !valid {
		return time.Time{}, false
	}
	return paymentCategoryCalendarDate(year, month, day)
}

// Python's native ISO parser distinguishes a terminal clock from a clock
// followed by a timezone. In the latter, a successful component prefix can
// leave a final separator (or suffix after six fractional digits). Preserve
// that pinned behavior instead of widening every input with whitespace trims.
func paymentCategoryISOClock(value string, followedByZone bool) (hour, minute, second, micros int, ok bool) {
	components := [3]int{}
	pos := 0
	separated := false
	for i := 0; i < 3; i++ {
		if pos+2 > len(value) || !paymentCategoryASCIIDigits(value[pos:pos+2]) {
			return
		}
		components[i], _ = strconv.Atoi(value[pos : pos+2])
		pos += 2
		var next byte
		if pos < len(value) {
			next = value[pos]
		}
		pos++
		if i == 0 {
			separated = next == ':'
		}
		if next == '.' || next == ',' {
			if pos >= len(value) {
				return
			}
			break
		}
		if pos >= len(value) {
			if next != 0 && !followedByZone {
				return
			}
			return components[0], components[1], components[2], 0, true
		}
		if separated {
			if next != ':' {
				return
			}
		} else {
			pos--
		}
	}
	remaining := len(value) - pos
	if remaining < 1 {
		return
	}
	count := remaining
	if count > 6 {
		count = 6
	}
	fraction := value[pos : pos+count]
	if !paymentCategoryASCIIDigits(fraction) {
		return
	}
	micros, _ = strconv.Atoi(fraction + strings.Repeat("0", 6-count))
	pos += count
	for pos < len(value) && value[pos] >= '0' && value[pos] <= '9' {
		pos++
	}
	if pos != len(value) && !followedByZone {
		return
	}
	return components[0], components[1], components[2], micros, true
}

// Python's Unicode \s includes the four information separators in addition to
// the Unicode White_Space property used by Go. Its \d accepts decimal digits.
var paymentCategoryDjangoDateTimePattern = regexp.MustCompile(`^([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})[T ]([0-9]{1,2}):([0-9]{1,2})(?::([0-9]{1,2})(?:[.,]([0-9]{1,12}))?)?[\t\n\v\f\r \x1c-\x1f\x{0085}\x{00a0}\x{1680}\x{2000}-\x{200a}\x{2028}\x{2029}\x{202f}\x{205f}\x{3000}]*(Z|[+-][0-9]{2}(?::?[0-9]{2})?)?$`)

func paymentCategoryFallbackDateTime(value string) (time.Time, bool) {
	normalized := strings.Map(func(r rune) rune {
		if r >= '0' && r <= '9' || !unicode.IsDigit(r) {
			return r
		}
		for _, ranges := range unicode.Digit.R16 {
			if uint32(r) >= uint32(ranges.Lo) && uint32(r) <= uint32(ranges.Hi) && (uint32(r)-uint32(ranges.Lo))%uint32(ranges.Stride) == 0 {
				return '0' + rune(((uint32(r)-uint32(ranges.Lo))/uint32(ranges.Stride))%10)
			}
		}
		for _, ranges := range unicode.Digit.R32 {
			if uint32(r) >= ranges.Lo && uint32(r) <= ranges.Hi && (uint32(r)-ranges.Lo)%ranges.Stride == 0 {
				return '0' + rune(((uint32(r)-ranges.Lo)/ranges.Stride)%10)
			}
		}
		return r
	}, value)
	match := paymentCategoryDjangoDateTimePattern.FindStringSubmatch(normalized)
	if match == nil && strings.HasSuffix(normalized, "\n") {
		// Python's dollar anchor also matches before one final newline.
		match = paymentCategoryDjangoDateTimePattern.FindStringSubmatch(strings.TrimSuffix(normalized, "\n"))
	}
	if match == nil {
		return time.Time{}, false
	}
	numbers := [7]int{}
	for i := 1; i <= 6; i++ {
		numbers[i-1], _ = strconv.Atoi(match[i])
	}
	if fraction := match[7]; fraction != "" {
		if len(fraction) > 6 {
			fraction = fraction[:6]
		}
		numbers[6], _ = strconv.Atoi(fraction + strings.Repeat("0", 6-len(fraction)))
	}
	date, ok := paymentCategoryCalendarDate(numbers[0], numbers[1], numbers[2])
	if !ok || numbers[3] > 23 || numbers[4] > 59 || numbers[5] > 59 {
		return time.Time{}, false
	}
	var offset time.Duration
	if zone := match[8]; zone != "" && zone != "Z" {
		hours, _ := strconv.Atoi(zone[1:3])
		minutes := 0
		if len(zone) > 3 {
			minutes, _ = strconv.Atoi(zone[len(zone)-2:])
		}
		offset = time.Duration(hours)*time.Hour + time.Duration(minutes)*time.Minute
		if offset >= 24*time.Hour {
			return time.Time{}, false
		}
		if zone[0] == '-' {
			offset = -offset
		}
	}
	return paymentCategoryUTCDateTime(date, numbers[3], numbers[4], numbers[5], numbers[6], offset)
}

func paymentCategoryCalendarDate(year, month, day int) (time.Time, bool) {
	if year < 1 || year > 9999 || month < 1 || month > 12 || day < 1 || day > 31 {
		return time.Time{}, false
	}
	date := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
	return date, int(date.Month()) == month && date.Day() == day
}

func paymentCategoryUTCDateTime(date time.Time, hour, minute, second, micros int, offset time.Duration) (time.Time, bool) {
	parsed := date.Add(time.Duration(hour)*time.Hour + time.Duration(minute)*time.Minute + time.Duration(second)*time.Second + time.Duration(micros)*time.Microsecond - offset)
	return parsed, true
}

func paymentCategoryDigits(value string) (int, bool) {
	if !paymentCategoryASCIIDigits(value) {
		return 0, false
	}
	result, err := strconv.Atoi(value)
	return result, err == nil
}

func paymentCategoryASCIIDigits(value string) bool {
	if value == "" {
		return false
	}
	for i := range value {
		if value[i] < '0' || value[i] > '9' {
			return false
		}
	}
	return true
}
