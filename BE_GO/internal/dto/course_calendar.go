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

// DjangoDateTime is the outcome of django.utils.dateparse.parse_datetime.
type DjangoDateTime int

const (
	DjangoDateTimeParsed DjangoDateTime = iota
	// DjangoDateTimeNone: neither parser matched (the function returns None).
	DjangoDateTimeNone
	// DjangoDateTimeValueError: the fallback pattern matched impossible values.
	DjangoDateTimeValueError
	// DjangoDateTimeNotPorted: Python accepts it but its UTC instant is outside
	// years 1..9999, which Go does not reproduce.
	DjangoDateTimeNotPorted
)

// DjangoParseDateTime is parse_datetime(value) for a str: Python 3.13's
// datetime.fromisoformat, then Django's pattern. It returns the UTC instant
// and whether the Python value is timezone-aware (naive values are UTC here).
func DjangoParseDateTime(value string) (time.Time, bool, DjangoDateTime) {
	moment, aware, result := DjangoParseDateTimeWide(value)
	if result == DjangoDateTimeParsed && DjangoDateTimeOutside(moment) {
		return time.Time{}, false, DjangoDateTimeNotPorted
	}
	return moment, aware, result
}

// DjangoDateTimeOutside reports a UTC instant Python's datetime cannot hold
// (before year 1 or after year 9999), which an aware value with an offset can
// have until it is converted.
func DjangoDateTimeOutside(moment time.Time) bool {
	return moment.Year() < 1 || moment.Year() > 9999
}

// DjangoParseDateTimeWide is DjangoParseDateTime that also returns instants
// outside years 1..9999 (as Parsed): Python keeps those as aware values with
// their own offset until something converts them.
func DjangoParseDateTimeWide(value string) (time.Time, bool, DjangoDateTime) {
	if parsed, ok := paymentCategoryISODateTime(value); ok {
		return parsed, isoDateTimeAware(value), DjangoDateTimeParsed
	}
	if parsed, ok := paymentCategoryFallbackDateTime(value); ok {
		match := djangoDateTimeFallbackMatch(value)
		return parsed, match != nil && match[8] != "", DjangoDateTimeParsed
	}
	if djangoDateTimeFallbackMatch(value) != nil {
		return time.Time{}, false, DjangoDateTimeValueError
	}
	return time.Time{}, false, DjangoDateTimeNone
}

// DjangoDateTimeWall is the wall-clock reading of an aware value parsed from
// value (instant plus its own offset), the fields Python's arithmetic works on.
func DjangoDateTimeWall(value string, instant time.Time) time.Time {
	text := PythonASCIIDigits(value)
	if strings.HasSuffix(text, "Z") {
		return instant
	}
	match := djangoWallOffset.FindStringSubmatch(text)
	if match == nil {
		return instant
	}
	offset := time.Duration(0)
	for i, unit := range []time.Duration{time.Hour, time.Minute, time.Second} {
		if match[i+2] != "" {
			number, _ := strconv.Atoi(match[i+2])
			offset += time.Duration(number) * unit
		}
	}
	if match[5] != "" {
		fraction := match[5] + strings.Repeat("0", 6-len(match[5]))
		number, _ := strconv.Atoi(fraction)
		offset += time.Duration(number) * time.Microsecond
	}
	if match[1] == "-" {
		offset = -offset
	}
	return instant.Add(offset)
}

var djangoWallOffset = regexp.MustCompile(`([+-])(\d\d)(?::?(\d\d)(?::?(\d\d)(?:\.(\d{1,6}))?)?)?$`)

// isoDateTimeAware: fromisoformat returns an aware datetime when the time
// part carries Z or an offset.
func isoDateTimeAware(value string) bool {
	runes := paymentCategoryDateTimeRunes(value)
	separator := paymentCategoryISOSeparator(runes)
	if separator < 0 || separator >= len(runes) {
		return false
	}
	return strings.ContainsAny(string(runes[separator+1:]), "+-Z")
}

func djangoDateTimeFallbackMatch(value string) []string {
	normalized := strings.Map(func(r rune) rune {
		if digit, ok := pythonDigit(r); ok {
			return '0' + rune(digit)
		}
		return r
	}, value)
	match := paymentCategoryDjangoDateTimePattern.FindStringSubmatch(normalized)
	if match == nil && strings.HasSuffix(normalized, "\n") {
		match = paymentCategoryDjangoDateTimePattern.FindStringSubmatch(strings.TrimSuffix(normalized, "\n"))
	}
	return match
}

// CalendarEventDateTime is global_calendar.event_datetime:
// datetime.fromisoformat(value.replace('Z', '+00:00')), naive values as UTC;
// ok=false is its ValidationError('Invalid calendar date.').
func CalendarEventDateTime(value any) (time.Time, bool) {
	text, ok := value.(string)
	if !ok {
		return time.Time{}, false
	}
	return paymentCategoryISODateTime(strings.ReplaceAll(text, "Z", "+00:00"))
}

// PythonIntResult classifies int(value).
type PythonIntResult int

const (
	PythonIntOK PythonIntResult = iota
	// PythonIntInvalid is TypeError or ValueError.
	PythonIntInvalid
	// PythonIntOverflow is OverflowError (int of an infinite float).
	PythonIntOverflow
)

// PythonInt is int(value) for a parsed request value.
func PythonInt(value any) (*big.Int, PythonIntResult) {
	switch v := value.(type) {
	case bool:
		if v {
			return big.NewInt(1), PythonIntOK
		}
		return big.NewInt(0), PythonIntOK
	case json.Number:
		text := string(v)
		if !strings.ContainsAny(text, ".eE") {
			number, ok := new(big.Int).SetString(text, 10)
			if !ok {
				return nil, PythonIntInvalid
			}
			return number, PythonIntOK
		}
		number, ok := new(big.Float).SetPrec(0).SetString(text)
		if !ok {
			return nil, PythonIntInvalid
		}
		float, _ := number.Float64()
		if math.IsInf(float, 0) {
			return nil, PythonIntOverflow
		}
		if math.IsNaN(float) {
			return nil, PythonIntInvalid
		}
		integer, _ := new(big.Float).SetFloat64(math.Trunc(float)).Int(nil)
		return integer, PythonIntOK
	case string:
		if number, ok := pythonIntString(v); ok {
			return number, PythonIntOK
		}
	}
	return nil, PythonIntInvalid
}

// PythonIsSpace is str.isspace() for one code point.
func PythonIsSpace(r rune) bool {
	return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f)
}

// PythonStrip is str.strip().
func PythonStrip(value string) string { return strings.TrimFunc(value, PythonIsSpace) }

// PythonEqual is == between parsed JSON values.
func PythonEqual(left, right any) bool {
	if a, ok := pythonNumeric(left); ok {
		b, ok := pythonNumeric(right)
		return ok && a.Cmp(b) == 0
	}
	switch a := left.(type) {
	case nil:
		return right == nil
	case string:
		b, ok := right.(string)
		return ok && a == b
	case domain.RequestObject:
		b, ok := right.(domain.RequestObject)
		if !ok || len(a) != len(b) {
			return false
		}
		for _, field := range a {
			value, present := b.Lookup(field.Name)
			if !present || !PythonEqual(field.Value, value) {
				return false
			}
		}
		return true
	case []any:
		b, ok := right.([]any)
		if !ok || len(a) != len(b) {
			return false
		}
		for i := range a {
			if !PythonEqual(a[i], b[i]) {
				return false
			}
		}
		return true
	}
	return false
}

// pythonNumeric is the exact value of a bool or number (NaN never equals).
func pythonNumeric(value any) (*big.Rat, bool) {
	switch v := value.(type) {
	case bool:
		if v {
			return big.NewRat(1, 1), true
		}
		return new(big.Rat), true
	case json.Number:
		text := string(v)
		if !strings.ContainsAny(text, ".eE") {
			number, ok := new(big.Rat).SetString(text)
			return number, ok
		}
		float, err := v.Float64()
		if err != nil || math.IsInf(float, 0) || math.IsNaN(float) {
			return nil, false
		}
		return new(big.Rat).SetFloat64(float), true
	}
	return nil, false
}

// PythonCopy deep-copies a parsed JSON value (Python lists and dicts are
// mutable; the views mutate their own copies).
func PythonCopy(value any) any {
	switch v := value.(type) {
	case domain.RequestObject:
		out := make(domain.RequestObject, len(v))
		for i, field := range v {
			out[i] = domain.RequestField{Name: field.Name, Value: PythonCopy(field.Value)}
		}
		return out
	case []any:
		out := make([]any, len(v))
		for i, item := range v {
			out[i] = PythonCopy(item)
		}
		return out
	}
	return value
}

// PythonSet is dict[key] = value: an existing key keeps its position.
func PythonSet(object domain.RequestObject, key string, value any) domain.RequestObject {
	for i, field := range object {
		if field.Name == key {
			object[i].Value = value
			return object
		}
	}
	return append(object, domain.RequestField{Name: key, Value: value})
}
