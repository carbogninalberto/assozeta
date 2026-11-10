package pydateutil

import (
	"math/big"
	"strings"
	"unicode/utf8"
)

// parserinfo tables (keys are the lowercase forms, as parserinfo._convert).
var (
	jumpWords = map[string]bool{" ": true, ".": true, ",": true, ";": true, "-": true, "/": true, "'": true,
		"at": true, "on": true, "and": true, "ad": true, "m": true, "t": true, "of": true,
		"st": true, "nd": true, "rd": true, "th": true}
	weekdayWords = map[string]int{"mon": 0, "monday": 0, "tue": 1, "tuesday": 1, "wed": 2, "wednesday": 2,
		"thu": 3, "thursday": 3, "fri": 4, "friday": 4, "sat": 5, "saturday": 5, "sun": 6, "sunday": 6}
	monthWords = map[string]int64{"jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
		"apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8,
		"sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11,
		"dec": 12, "december": 12}
	hmsWords = map[string]int{"h": 0, "hour": 0, "hours": 0, "m": 1, "minute": 1, "minutes": 1,
		"s": 2, "second": 2, "seconds": 2}
	ampmWords    = map[string]int{"am": 0, "a": 0, "pm": 1, "p": 1}
	utcZoneWords = map[string]bool{"utc": true, "gmt": true, "z": true}
)

func isJump(token string) bool {
	word, ok := lowerASCII(token)
	return ok && jumpWords[word]
}

// lookup returns the table value of token, or -1.
func lookup(table map[string]int, token string) int {
	if word, ok := lowerASCII(token); ok {
		if v, found := table[word]; found {
			return v
		}
	}
	return -1
}

// monthOf is parserinfo.month: 1..12, or 0.
func monthOf(token string) int64 {
	word, _ := lowerASCII(token)
	return monthWords[word]
}

func isPertain(token string) bool {
	word, _ := lowerASCII(token)
	return word == "of"
}

func isUTCZone(token string) bool {
	word, ok := lowerASCII(token)
	return ok && utcZoneWords[word]
}

// parsed is parser._result: nil and -1 stand for None.
type parsed struct {
	year, month, day     *big.Int
	weekday              int
	hour, minute, second *big.Int
	microsecond          int
	hasMicrosecond       bool
	tzname               string
	hasTZName            bool
	tzoffset             *big.Int
	ampm                 int
	centurySpecified     bool
}

// empty is `len(res) == 0`.
func (r *parsed) empty() bool {
	return r.year == nil && r.month == nil && r.day == nil && r.weekday < 0 && r.hour == nil && r.minute == nil &&
		r.second == nil && !r.hasMicrosecond && !r.hasTZName && r.tzoffset == nil && r.ampm < 0
}

// ymd is _ymd: the collected year/month/day candidates.
type ymd struct {
	values           []*big.Int
	centurySpecified bool
	dayIdx           int
	monthIdx         int
	yearIdx          int
}

// label assigns the role of the value just appended; false is the
// ValueError for a role already taken.
func (y *ymd) label(label byte) bool {
	idx := len(y.values) - 1
	switch label {
	case 'M':
		if y.monthIdx >= 0 {
			return false
		}
		y.monthIdx = idx
	case 'D':
		if y.dayIdx >= 0 {
			return false
		}
		y.dayIdx = idx
	case 'Y':
		if y.yearIdx >= 0 {
			return false
		}
		y.yearIdx = idx
	}
	return true
}

// appendString is _ymd.append for a str.
func (y *ymd) appendString(value string, label byte) bool {
	if isDigitString(value) && utf8.RuneCountInString(value) > 2 {
		y.centurySpecified = true
		if label != 0 && label != 'Y' {
			return false
		}
		label = 'Y'
	}
	v, ok := pyInt(value)
	if !ok {
		return false
	}
	y.values = append(y.values, v)
	return y.label(label)
}

// appendDecimal is _ymd.append for a Decimal without label.
func (y *ymd) appendDecimal(value decimal) bool {
	label := byte(0)
	if value.greater(100) {
		y.centurySpecified = true
		label = 'Y'
	}
	y.values = append(y.values, new(big.Int).Set(value.ip))
	return y.label(label)
}

// appendMonth is _ymd.append(month, 'M') for a month number.
func (y *ymd) appendMonth(month int64) bool {
	y.values = append(y.values, small(month))
	return y.label('M')
}

func (y *ymd) couldBeDay(value decimal) bool {
	switch {
	case y.dayIdx >= 0:
		return false
	case y.monthIdx < 0:
		return value.ip.Sign() > 0 && value.lessEqual(31)
	case y.yearIdx < 0:
		return value.ip.Sign() > 0 && value.lessEqual(daysInMonth(small(2000), y.values[y.monthIdx].Int64()))
	default:
		return value.ip.Sign() > 0 && value.lessEqual(daysInMonth(y.values[y.yearIdx], y.values[y.monthIdx].Int64()))
	}
}

// resolve is _ymd.resolve_ymd(yearfirst=False, dayfirst=dayFirst); false is
// the ValueError for more than three values.
func (y *ymd) resolve(dayFirst bool) (year, month, day *big.Int, ok bool) {
	v := y.values
	n := len(v)
	labelled := 0
	for _, idx := range []int{y.yearIdx, y.monthIdx, y.dayIdx} {
		if idx >= 0 {
			labelled++
		}
	}
	if (n == labelled && labelled > 0) || (n == 3 && labelled == 2) {
		yi, mi, di := y.yearIdx, y.monthIdx, y.dayIdx
		if n == 3 && labelled == 2 {
			missing := 3 - max(yi, 0) - max(mi, 0) - max(di, 0)
			switch {
			case yi < 0:
				yi = missing
			case mi < 0:
				mi = missing
			default:
				di = missing
			}
		}
		pick := func(idx int) *big.Int {
			if idx < 0 {
				return nil
			}
			return v[idx]
		}
		return pick(yi), pick(mi), pick(di), true
	}
	gt31 := func(x *big.Int) bool { return x.Cmp(small(31)) > 0 }
	le12 := func(x *big.Int) bool { return x.Cmp(small(12)) <= 0 }
	mi := y.monthIdx
	switch {
	case n > 3:
		return nil, nil, nil, false
	case n == 1 || (mi >= 0 && n == 2):
		var other *big.Int
		if mi >= 0 {
			month = v[mi]
			other = v[1-mi]
		} else {
			other = v[0]
		}
		if n > 1 || mi < 0 {
			if gt31(other) {
				year = other
			} else {
				day = other
			}
		}
	case n == 2:
		switch {
		case gt31(v[0]):
			year, month = v[0], v[1]
		case gt31(v[1]):
			month, year = v[0], v[1]
		case dayFirst && le12(v[1]):
			day, month = v[0], v[1]
		default:
			month, day = v[0], v[1]
		}
	case n == 3:
		switch mi {
		case 0:
			if gt31(v[1]) {
				month, year, day = v[0], v[1], v[2]
			} else {
				month, day, year = v[0], v[1], v[2]
			}
		case 1:
			if gt31(v[0]) {
				year, month, day = v[0], v[1], v[2]
			} else {
				day, month, year = v[0], v[1], v[2]
			}
		case 2:
			if gt31(v[1]) {
				day, year, month = v[0], v[1], v[2]
			} else {
				year, day, month = v[0], v[1], v[2]
			}
		default:
			switch {
			case gt31(v[0]) || y.yearIdx == 0:
				if dayFirst && le12(v[2]) {
					year, day, month = v[0], v[1], v[2]
				} else {
					year, month, day = v[0], v[1], v[2]
				}
			case !le12(v[0]) || (dayFirst && le12(v[1])):
				day, month, year = v[0], v[1], v[2]
			default:
				month, day, year = v[0], v[1], v[2]
			}
		}
	}
	return year, month, day, true
}

// parser is one run of parser._parse.
type parser struct {
	pivot    int64
	dayFirst bool
	res      parsed
	ymd      ymd
	raised   *Error // an exception _parse does not catch
}

// convertYear is parserinfo.convertyear.
func (p *parser) convertYear(year *big.Int, centurySpecified bool) *big.Int {
	if centurySpecified || year.Cmp(small(100)) >= 0 {
		return year
	}
	v := year.Int64() + p.pivot/100*100
	if v >= p.pivot+50 {
		v -= 100
	} else if v < p.pivot-50 {
		v += 100
	}
	return small(v)
}

func adjustAMPM(hour *big.Int, ampm int) *big.Int {
	switch c := hour.Cmp(small(12)); {
	case c < 0 && ampm == 1:
		return new(big.Int).Add(hour, small(12))
	case c == 0 && ampm == 0:
		return small(0)
	}
	return hour
}

func (p *parser) couldBeTZName(token string) bool {
	if p.res.hour == nil || p.res.hasTZName || utf8.RuneCountInString(token) > 5 {
		return false
	}
	switch token {
	case "UTC", "GMT", "Z", "z":
		return true
	}
	for i := 0; i < len(token); i++ {
		if token[i] < 'A' || token[i] > 'Z' {
			return false
		}
	}
	return true
}

// parse is parser._parse followed by parserinfo.validate. It returns false
// where _parse returns None (a caught ValueError or IndexError) or raises
// (p.raised).
func (p *parser) parse(text string) bool {
	p.res = parsed{weekday: -1, ampm: -1}
	p.ymd = ymd{dayIdx: -1, monthIdx: -1, yearIdx: -1}
	res := &p.res
	l := timelex(text)
	n := len(l)
	for i := 0; i < n; i++ {
		token := l[i]
		if isFloat(token) {
			next, ok := p.numericToken(l, i)
			if !ok {
				return false
			}
			i = next
		} else if weekday := lookup(weekdayWords, token); weekday >= 0 {
			res.weekday = weekday
		} else if month := monthOf(token); month > 0 {
			if !p.ymd.appendMonth(month) {
				return false
			}
			if i+1 < n {
				if l[i+1] == "-" || l[i+1] == "/" {
					// Jan-01[-99]
					sep := l[i+1]
					if i+2 >= n || !p.ymd.appendString(l[i+2], 0) {
						return false
					}
					if i+3 < n && l[i+3] == sep {
						if i+4 >= n || !p.ymd.appendString(l[i+4], 0) {
							return false
						}
						i += 2
					}
					i += 2
				} else if i+4 < n && l[i+1] == " " && l[i+3] == " " && isPertain(l[i+2]) {
					// Jan of 01: the number is the year.
					if isDigitString(l[i+4]) {
						value, ok := pyInt(l[i+4])
						if !ok || !p.ymd.appendString(p.convertYear(value, false).String(), 'Y') {
							return false
						}
					}
					i += 4
				}
			}
		} else if ampm := lookup(ampmWords, token); ampm >= 0 {
			if res.hour == nil || res.hour.Cmp(small(12)) > 0 {
				return false
			}
			res.hour = adjustAMPM(res.hour, ampm)
			res.ampm = ampm
		} else if res.tzoffset == nil && p.couldBeTZName(token) {
			res.tzname, res.hasTZName = token, true
			// parserinfo.tzoffset compares the name as written with the
			// lowercase UTC names: only 'z' matches.
			if utcZoneWords[token] {
				res.tzoffset = small(0)
			}
			if i+1 < n && (l[i+1] == "+" || l[i+1] == "-") {
				// GMT+3 means "my time + 3 is GMT": the sign is reversed.
				if l[i+1] == "+" {
					l[i+1] = "-"
				} else {
					l[i+1] = "+"
				}
				res.tzoffset = nil
				if isUTCZone(res.tzname) {
					res.tzname, res.hasTZName = "", false
				}
			}
		} else if res.hour != nil && (token == "+" || token == "-") {
			if i+1 >= n {
				return false
			}
			next := []rune(l[i+1])
			var hours, minutes *big.Int
			var ok bool
			switch {
			case len(next) == 4:
				// -0300
				if hours, ok = pyInt(string(next[:2])); !ok {
					return false
				}
				if minutes, ok = pyInt(string(next[2:])); !ok {
					return false
				}
			case i+2 < n && l[i+2] == ":":
				// -03:00
				if hours, ok = pyInt(l[i+1]); !ok || i+3 >= n {
					return false
				}
				if minutes, ok = pyInt(l[i+3]); !ok {
					return false
				}
				i += 2
			case len(next) <= 2:
				// -[0]3
				if hours, ok = pyInt(l[i+1]); !ok {
					return false
				}
				minutes = small(0)
			default:
				return false
			}
			offset := hours.Mul(hours, small(3600))
			offset.Add(offset, minutes.Mul(minutes, small(60)))
			if token == "-" {
				offset.Neg(offset)
			}
			res.tzoffset = offset
			// -0300 (BRST)
			if i+5 < n && isJump(l[i+2]) && l[i+3] == "(" && l[i+5] == ")" &&
				utf8.RuneCountInString(l[i+4]) >= 3 && p.couldBeTZName(l[i+4]) {
				res.tzname, res.hasTZName = l[i+4], true
				i += 4
			}
			i++
		} else if !isJump(token) {
			return false
		}
	}
	year, month, day, ok := p.ymd.resolve(p.dayFirst)
	if !ok {
		return false
	}
	res.centurySpecified = p.ymd.centurySpecified
	res.year, res.month, res.day = year, month, day

	// parserinfo.validate
	if res.year != nil {
		res.year = p.convertYear(res.year, res.centurySpecified)
	}
	zeroOffset := res.tzoffset != nil && res.tzoffset.Sign() == 0
	if (zeroOffset && !res.hasTZName) || res.tzname == "Z" || res.tzname == "z" {
		res.tzname, res.hasTZName = "UTC", true
		res.tzoffset = small(0)
	} else if !zeroOffset && res.hasTZName && isUTCZone(res.tzname) {
		res.tzoffset = small(0)
	}
	return true
}

// parseMS is parser._parsems.
func parseMS(value string) (*big.Int, int, bool) {
	if !strings.Contains(value, ".") {
		seconds, ok := pyInt(value)
		return seconds, 0, ok
	}
	parts := strings.Split(value, ".")
	if len(parts) != 2 {
		return nil, 0, false
	}
	seconds, ok := pyInt(parts[0])
	if !ok {
		return nil, 0, false
	}
	fraction := []rune(parts[1])
	for len(fraction) < 6 {
		fraction = append(fraction, '0')
	}
	micro, ok := pyInt(string(fraction[:6]))
	if !ok {
		return nil, 0, false
	}
	return seconds, int(micro.Int64()), true
}

// minSec is parser._parse_min_sec; it assigns minute and second (None for a
// whole number of minutes).
func (p *parser) minSec(value decimal) bool {
	p.res.minute = new(big.Int).Set(value.ip)
	p.res.second = nil
	fraction, err := value.rem1()
	if err != nil {
		p.raised = err
		return false
	}
	if fraction {
		p.res.second = value.sixtyFrac()
	}
	return true
}

func (p *parser) setSeconds(value string) bool {
	seconds, micro, ok := parseMS(value)
	if !ok {
		return false
	}
	p.res.second, p.res.microsecond, p.res.hasMicrosecond = seconds, micro, true
	return true
}

// findHMS is parser._find_hms_idx with allow_jump=True: the index of the
// h/m/s label that applies to tokens[idx], or -1.
func findHMS(idx int, tokens []string) int {
	n := len(tokens)
	switch {
	case idx+1 < n && lookup(hmsWords, tokens[idx+1]) >= 0:
		return idx + 1
	case idx+2 < n && tokens[idx+1] == " " && lookup(hmsWords, tokens[idx+2]) >= 0:
		return idx + 2
	case idx > 0 && lookup(hmsWords, tokens[idx-1]) >= 0:
		return idx - 1
	case 1 < idx && idx == n-1 && tokens[idx-1] == " " && lookup(hmsWords, tokens[idx-2]) >= 0:
		return idx - 2
	}
	return -1
}

// numericToken is parser._parse_numeric_token; it returns the index of the
// last token consumed.
func (p *parser) numericToken(tokens []string, idx int) (int, bool) {
	res := &p.res
	repr := tokens[idx]
	value, ok := parseDecimal(repr)
	if !ok {
		return 0, false
	}
	chars := []rune(repr)
	size := len(chars)
	n := len(tokens)
	sub := func(from, to int) string { return string(chars[min(from, size):min(to, size)]) }
	dot := -1
	for i, c := range chars {
		if c == '.' {
			dot = i
			break
		}
	}
	setInt := func(target **big.Int, text string) bool {
		v, ok := pyInt(text)
		if ok {
			*target = v
		}
		return ok
	}

	switch {
	case len(p.ymd.values) == 3 && (size == 2 || size == 4) && res.hour == nil &&
		(idx+1 >= n || (tokens[idx+1] != ":" && lookup(hmsWords, tokens[idx+1]) < 0)):
		// 19990101T23[59]
		if !setInt(&res.hour, sub(0, 2)) {
			return 0, false
		}
		if size == 4 && !setInt(&res.minute, sub(2, size)) {
			return 0, false
		}

	case size == 6 || (size > 6 && dot == 6):
		// YYMMDD or HHMMSS[.ss]
		if len(p.ymd.values) == 0 && dot < 0 {
			if !p.ymd.appendString(sub(0, 2), 0) || !p.ymd.appendString(sub(2, 4), 0) ||
				!p.ymd.appendString(sub(4, size), 0) {
				return 0, false
			}
		} else if !setInt(&res.hour, sub(0, 2)) || !setInt(&res.minute, sub(2, 4)) || !p.setSeconds(sub(4, size)) {
			return 0, false
		}

	case size == 8 || size == 12 || size == 14:
		// YYYYMMDD[hhmm[ss]]
		if !p.ymd.appendString(sub(0, 4), 'Y') || !p.ymd.appendString(sub(4, 6), 0) ||
			!p.ymd.appendString(sub(6, 8), 0) {
			return 0, false
		}
		if size > 8 {
			if !setInt(&res.hour, sub(8, 10)) || !setInt(&res.minute, sub(10, 12)) {
				return 0, false
			}
			if size > 12 && !setInt(&res.second, sub(12, size)) {
				return 0, false
			}
		}

	case findHMS(idx, tokens) >= 0:
		// HH[ ]h or MM[ ]m or SS[.ss][ ]s
		hmsIdx := findHMS(idx, tokens)
		hms := lookup(hmsWords, tokens[hmsIdx])
		if hmsIdx > idx {
			idx = hmsIdx
		} else {
			// The label precedes the number: it is the next smaller unit.
			hms++
		}
		switch hms {
		case 0:
			res.hour = new(big.Int).Set(value.ip)
			fraction, err := value.rem1()
			if err != nil {
				p.raised = err
				return 0, false
			}
			if fraction {
				res.minute = value.sixtyFrac()
			}
		case 1:
			if !p.minSec(value) {
				return 0, false
			}
		case 2:
			if !p.setSeconds(repr) {
				return 0, false
			}
		}

	case idx+2 < n && tokens[idx+1] == ":":
		// HH:MM[:SS[.ss]]
		res.hour = new(big.Int).Set(value.ip)
		minutes, ok := parseDecimal(tokens[idx+2])
		if !ok || !p.minSec(minutes) {
			return 0, false
		}
		if idx+4 < n && tokens[idx+3] == ":" {
			if !p.setSeconds(tokens[idx+4]) {
				return 0, false
			}
			idx += 2
		}
		idx += 2

	case idx+1 < n && (tokens[idx+1] == "-" || tokens[idx+1] == "/" || tokens[idx+1] == "."):
		sep := tokens[idx+1]
		if !p.ymd.appendString(repr, 0) {
			return 0, false
		}
		if idx+2 < n && !isJump(tokens[idx+2]) {
			if isDigitString(tokens[idx+2]) {
				// 01-01[-01]
				if !p.ymd.appendString(tokens[idx+2], 0) {
					return 0, false
				}
			} else if month := monthOf(tokens[idx+2]); month == 0 || !p.ymd.appendMonth(month) {
				// 01-Jan[-01]
				return 0, false
			}
			if idx+3 < n && tokens[idx+3] == sep {
				// Three members.
				if idx+4 >= n {
					return 0, false
				}
				if month := monthOf(tokens[idx+4]); month > 0 {
					if !p.ymd.appendMonth(month) {
						return 0, false
					}
				} else if !p.ymd.appendString(tokens[idx+4], 0) {
					return 0, false
				}
				idx += 2
			}
			idx++
		}
		idx++

	case idx+1 >= n || isJump(tokens[idx+1]):
		if idx+2 < n && lookup(ampmWords, tokens[idx+2]) >= 0 {
			// 12 am
			res.hour = adjustAMPM(new(big.Int).Set(value.ip), lookup(ampmWords, tokens[idx+2]))
			idx++
		} else if !p.ymd.appendDecimal(value) {
			// Year, month or day.
			return 0, false
		}
		idx++

	case lookup(ampmWords, tokens[idx+1]) >= 0 && value.ip.Cmp(small(24)) < 0:
		// 12am
		res.hour = adjustAMPM(new(big.Int).Set(value.ip), lookup(ampmWords, tokens[idx+1]))
		idx++

	case p.ymd.couldBeDay(value):
		if !p.ymd.appendDecimal(value) {
			return 0, false
		}

	default:
		return 0, false
	}
	return idx, true
}
