package dto

import (
	"encoding/json"
	"math"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// PythonFloat renders a Python float as Python's json module does (repr).
type PythonFloat float64

func (f PythonFloat) MarshalJSON() ([]byte, error) { return []byte(pythonFloatRepr(float64(f))), nil }

// PythonFloatSum is CPython 3.12+ sum() over floats starting from the integer
// 0: the first float is added plainly, then Neumaier compensation is applied
// and the compensation is added at the end when non-zero and finite.
func PythonFloatSum(values []float64) float64 {
	if len(values) == 0 {
		return 0
	}
	total, compensation := 0+values[0], 0.0
	for _, value := range values[1:] {
		t := total + value
		if math.Abs(total) >= math.Abs(value) {
			compensation += (total - t) + value
		} else {
			compensation += (value - t) + total
		}
		total = t
	}
	if compensation != 0 && !math.IsInf(compensation, 0) && !math.IsNaN(compensation) {
		total += compensation
	}
	return total
}

// DecimalNumber is DRF's JSONEncoder for a Decimal (float(value)); nil is None.
func DecimalNumber(text *string) json.RawMessage {
	if text == nil {
		return json.RawMessage("null")
	}
	value, err := strconv.ParseFloat(*text, 64)
	if err != nil {
		return nil
	}
	return json.RawMessage(pythonFloatRepr(value))
}

// DecimalOrZero is `Decimal or 0`: None and a zero Decimal both become the integer 0.
func DecimalOrZero(text *string) json.RawMessage {
	if text == nil {
		return json.RawMessage("0")
	}
	if value, ok := new(big.Rat).SetString(*text); ok && value.Sign() == 0 {
		return json.RawMessage("0")
	}
	return DecimalNumber(text)
}

// PythonJSONFinite reports whether every number of a parsed reference value is
// a finite Python value (json.loads turns overflowing floats into inf, which
// DRF's strict renderer refuses).
func PythonJSONFinite(value any) bool {
	switch v := value.(type) {
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			parsed, err := strconv.ParseFloat(string(v), 64)
			return err == nil && !math.IsInf(parsed, 0)
		}
		return true
	case []any:
		for _, item := range v {
			if !PythonJSONFinite(item) {
				return false
			}
		}
	case domain.RequestObject:
		for _, field := range v {
			if !PythonJSONFinite(field.Value) {
				return false
			}
		}
	}
	return true
}

// PythonNumberRat is the exact value Python compares for a parsed JSON number:
// an int token exactly, a float token as the nearest double.
func PythonNumberRat(number json.Number) (*big.Rat, bool) {
	text := string(number)
	if strings.ContainsAny(text, ".eE") {
		value, err := strconv.ParseFloat(text, 64)
		if err != nil || math.IsInf(value, 0) {
			return nil, false
		}
		return new(big.Rat).SetFloat64(value), true
	}
	value, ok := new(big.Int).SetString(text, 10)
	if !ok {
		return nil, false
	}
	return new(big.Rat).SetInt(value), true
}

// StrptimeUnicodeDigits reports characters Python's \d accepts but this port
// does not parse (non-ASCII decimal digits); callers refuse such input.
func StrptimeUnicodeDigits(text string) bool {
	for _, r := range text {
		if r > unicode.MaxASCII && unicode.Is(unicode.Nd, r) {
			return true
		}
	}
	return false
}

const strptimeDate = `(\p{Nd}\p{Nd}\p{Nd}\p{Nd})-(1[0-2]|0[1-9]|[1-9])-(3[0-1]|[1-2]\p{Nd}|0[1-9]|[1-9]| [1-9])`
const strptimeClock = `(2[0-3]|[0-1]\p{Nd}|\p{Nd}):([0-5]\p{Nd}|\p{Nd}):(6[0-1]|[0-5]\p{Nd}|\p{Nd})`

// Python's \s for str patterns: ASCII whitespace, \x1c-\x1f and Unicode spaces.
const strptimeSpace = `[\t\n\v\f\r \x{1c}-\x{1f}\x{85}\x{a0}\x{1680}\x{2000}-\x{200a}\x{2028}\x{2029}\x{202f}\x{205f}\x{3000}]+`

var (
	eventStartPattern = regexp.MustCompile(`^` + strptimeDate + `[Tt]` + strptimeClock + `\.([0-9]{1,6})[Zz]`)
	lessonDatePattern = regexp.MustCompile(`^` + strptimeDate + strptimeSpace + strptimeClock + `([+-]\p{Nd}\p{Nd}:?[0-5]\p{Nd}(?::?[0-5]\p{Nd}(?:\.\p{Nd}{1,6})?)?|Z)`)
)

func strptimeFields(match []string) (year, month, day, hour, minute, second int, ok bool) {
	atoi := func(text string) int {
		value, _ := strconv.Atoi(PythonASCIIDigits(strings.TrimSpace(text)))
		return value
	}
	year, month, day = atoi(match[1]), atoi(match[2]), atoi(match[3])
	hour, minute, second = atoi(match[4]), atoi(match[5]), atoi(match[6])
	if year < 1 || second > 59 || day > time.Date(year, time.Month(month)+1, 0, 0, 0, 0, 0, time.UTC).Day() {
		return 0, 0, 0, 0, 0, 0, false
	}
	return year, month, day, hour, minute, second, true
}

// StrptimeEventStart is datetime.strptime(text, '%Y-%m-%dT%H:%M:%S.%fZ'), a
// naive value returned in UTC; false is Python's ValueError. Decimal digits
// of any script match, as in Python's \d.
func StrptimeEventStart(text string) (time.Time, bool) {
	match := eventStartPattern.FindStringSubmatch(text)
	if match == nil || len(match[0]) != len(text) {
		return time.Time{}, false
	}
	year, month, day, hour, minute, second, ok := strptimeFields(match)
	if !ok {
		return time.Time{}, false
	}
	fraction := match[7] + strings.Repeat("0", 6-len(match[7]))
	micro, _ := strconv.Atoi(fraction)
	return time.Date(year, time.Month(month), day, hour, minute, second, micro*1000, time.UTC), true
}

// StrptimeLessonDate is datetime.strptime(text, '%Y-%m-%d %H:%M:%S%z') as an
// aware instant; false is Python's ValueError.
func StrptimeLessonDate(text string) (time.Time, bool) {
	match := lessonDatePattern.FindStringSubmatch(text)
	if match == nil || len(match[0]) != len(text) {
		return time.Time{}, false
	}
	year, month, day, hour, minute, second, ok := strptimeFields(match)
	if !ok {
		return time.Time{}, false
	}
	offset, ok := strptimeOffset(match[7])
	if !ok {
		return time.Time{}, false
	}
	wall := time.Date(year, time.Month(month), day, hour, minute, second, 0, time.UTC)
	return wall.Add(-offset), true
}

// strptimeOffset follows _strptime's %z conversion and datetime.timezone's
// strict bound (|offset| < 24 hours).
func strptimeOffset(z string) (time.Duration, bool) {
	if z == "Z" {
		return 0, true
	}
	z = PythonASCIIDigits(z)
	if z[3] == ':' {
		z = z[:3] + z[4:]
		if len(z) > 5 {
			if z[5] != ':' {
				return 0, false
			}
			z = z[:5] + z[6:]
		}
	}
	hours, err1 := strconv.Atoi(z[1:3])
	minutes, err2 := strconv.Atoi(z[3:5])
	seconds := 0
	var err3 error
	if len(z) > 5 {
		end := 7
		if len(z) < end {
			end = len(z)
		}
		seconds, err3 = strconv.Atoi(z[5:end])
	}
	if err1 != nil || err2 != nil || err3 != nil {
		return 0, false
	}
	fraction := 0
	if len(z) > 8 {
		remainder := z[8:]
		value, err := strconv.Atoi(remainder + strings.Repeat("0", 6-len(remainder)))
		if err != nil {
			return 0, false
		}
		fraction = value
	}
	offset := time.Duration(hours)*time.Hour + time.Duration(minutes)*time.Minute + time.Duration(seconds)*time.Second +
		time.Duration(fraction)*time.Microsecond
	if z[0] == '-' {
		offset = -offset
	}
	if offset <= -24*time.Hour || offset >= 24*time.Hour {
		return 0, false
	}
	return offset, true
}
