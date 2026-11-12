package dto

import (
	"encoding/json"
	"fmt"
	"math"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// Values after _coerce_filter_values: Python date, naive datetime, int and
// float objects (strings that did not convert stay strings).
type (
	mcpCoercedDate     struct{ t time.Time }
	mcpCoercedDateTime struct{ t time.Time }
	mcpCoercedInt      struct{ v *big.Int }
	mcpCoercedFloat    struct{ v float64 }
)

var mcpIntegerClasses = map[string]bool{"IntegerField": true, "SmallIntegerField": true, "BigIntegerField": true,
	"PositiveIntegerField": true, "PositiveSmallIntegerField": true}

// mcpAllowedLookups is security.ALLOWED_LOOKUPS; mcpDateParts is
// DATE_PART_SUFFIXES.
var mcpAllowedLookups = map[string]bool{"exact": true, "iexact": true, "contains": true, "icontains": true, "gt": true,
	"gte": true, "lt": true, "lte": true, "in": true, "isnull": true, "startswith": true, "istartswith": true, "endswith": true,
	"iendswith": true, "range": true, "year": true, "month": true, "day": true, "date": true, "has_key": true}

var mcpDateParts = map[string]bool{"year": true, "month": true, "day": true, "quarter": true, "week": true, "week_day": true, "hour": true}

// mcpCoerce is _coerce_filter_values for one key: the target field found by
// walking the key (relations move to the related model) decides how a
// string value converts.
func mcpCoerce(model *mcpModel, key string, value any) any {
	text, ok := value.(string)
	if !ok {
		return value
	}
	current := model
	var target *mcpField
	for _, part := range strings.Split(key, "__") {
		if mcpAllowedLookups[part] || mcpDateParts[part] {
			break
		}
		field := current.getField(part)
		if field == nil {
			break
		}
		if field.Relation && field.relatedModel() != nil {
			current = field.relatedModel()
		} else {
			target = field
		}
	}
	if target == nil {
		return value
	}
	switch {
	case target.Class == "DateField" || target.Class == "DateTimeField":
		return mcpCoerceDate(text)
	case mcpIntegerClasses[target.Class]:
		if number, ok := PythonIntText(text); ok {
			return mcpCoercedInt{number}
		}
	case target.Class == "FloatField" || target.Class == "DecimalField":
		if number, ok := PythonFloatParse(text); ok {
			return mcpCoercedFloat{number}
		}
	}
	return value
}

// strptime patterns of _coerce_date_value's formats (re.IGNORECASE, full match).
var mcpStrptime = []struct {
	pattern *regexp.Regexp
	date    bool
}{
	{regexp.MustCompile(`(?i)^(\d\d\d\d)-(1[0-2]|0[1-9]|[1-9])-(3[01]|[12]\d|0[1-9]|[1-9]| [1-9])T(2[0-3]|[0-1]\d|\d):([0-5]\d|\d):(6[0-1]|[0-5]\d|\d)$`), false},
	{regexp.MustCompile(`(?i)^(\d\d\d\d)-(1[0-2]|0[1-9]|[1-9])-(3[01]|[12]\d|0[1-9]|[1-9]| [1-9])T(2[0-3]|[0-1]\d|\d):([0-5]\d|\d)()$`), false},
	{regexp.MustCompile(`(?i)^(\d\d\d\d)-(1[0-2]|0[1-9]|[1-9])-(3[01]|[12]\d|0[1-9]|[1-9]| [1-9])\s+(2[0-3]|[0-1]\d|\d):([0-5]\d|\d):(6[0-1]|[0-5]\d|\d)$`), false},
	{regexp.MustCompile(`(?i)^(\d\d\d\d)-(1[0-2]|0[1-9]|[1-9])-(3[01]|[12]\d|0[1-9]|[1-9]| [1-9])()()()$`), true},
}

// mcpCoerceDate is _coerce_date_value: the first strptime format that parses
// to a valid date or datetime.
func mcpCoerceDate(text string) any {
	for _, format := range mcpStrptime {
		match := format.pattern.FindStringSubmatch(text)
		if match == nil {
			continue
		}
		number := func(i int) int {
			value, _ := strconv.Atoi(strings.TrimSpace(match[i]))
			return value
		}
		year, month, day := number(1), number(2), number(3)
		hour, minute, second := number(4), number(5), number(6)
		if !mcpValidDate(year, month, day) || second > 59 {
			continue
		}
		moment := time.Date(year, time.Month(month), day, hour, minute, second, 0, time.UTC)
		if format.date {
			return mcpCoercedDate{moment}
		}
		return mcpCoercedDateTime{moment}
	}
	return text
}

func mcpValidDate(year, month, day int) bool {
	if year < 1 || year > 9999 || month < 1 || month > 12 || day < 1 {
		return false
	}
	return day <= time.Date(year, time.Month(month)+1, 0, 0, 0, 0, 0, time.UTC).Day()
}

type mcpPrepared struct {
	param  any
	cast   string
	isNull bool
	// integer is the Python int of an integer-kind value (comparison ranges).
	integer *big.Int
}

// prepare is the lookup's get_prep_value for the lhs output field (the
// relation target for related lookups, IntegerField for date parts,
// DateField for __date).
func (q *mcpQuery) prepare(lhs mcpExpr, value any, top bool) (mcpPrepared, error) {
	if value == nil {
		return mcpPrepared{isNull: true}, nil
	}
	if _, uncertain := value.(mcpUncertain); uncertain {
		return mcpPrepared{}, mcpNotPorted("non-ASCII digits")
	}
	kind, name := lhs.kind, "None"
	if lhs.field != nil && lhs.extract == "" {
		name = lhs.field.Name
	}
	switch kind {
	case "int":
		number, err := mcpIntValue(value, name)
		if err != nil {
			return mcpPrepared{}, err
		}
		return mcpPrepared{integer: number}, nil
	case "uuid":
		canonical, err := mcpUUIDValue(value)
		if err != nil {
			return mcpPrepared{}, err
		}
		return mcpPrepared{param: canonical, cast: "::uuid"}, nil
	case "text":
		text, err := mcpTextValue(value)
		if err != nil {
			return mcpPrepared{}, err
		}
		return mcpPrepared{param: text}, nil
	case "bool":
		return mcpBoolValue(lhs, value)
	case "decimal":
		return mcpDecimalValue(lhs, value)
	case "float":
		number, err := mcpFloatValue(value, name)
		if err != nil {
			return mcpPrepared{}, err
		}
		return mcpPrepared{param: number, cast: "::double precision"}, nil
	case "date":
		day, err := mcpDateValue(value)
		if err != nil {
			return mcpPrepared{}, err
		}
		return mcpPrepared{param: day, cast: "::date"}, nil
	case "datetime":
		moment, err := mcpDateTimeValue(value)
		if err != nil {
			return mcpPrepared{}, err
		}
		return mcpPrepared{param: moment, cast: "::timestamptz"}, nil
	case "time":
		moment, err := mcpTimeValue(value)
		if err != nil {
			return mcpPrepared{}, err
		}
		return mcpPrepared{param: moment, cast: "::time"}, nil
	}
	return mcpPrepared{}, mcpNotPorted("values for " + kind)
}

// mcpPreparedArgument is the value handed to the driver (integers fit int64).
func mcpPreparedArgument(prepared mcpPrepared) (any, error) {
	if prepared.integer != nil {
		if !prepared.integer.IsInt64() {
			return nil, mcpNotPorted("integer parameter outside 64 bits")
		}
		return prepared.integer.Int64(), nil
	}
	return prepared.param, nil
}

// mcpSmallInt is a Python int parameter the way psycopg adapts it: int2/int4
// when it fits, int8 otherwise.
func mcpSmallInt(number *big.Int) (any, error) {
	if !number.IsInt64() {
		return nil, mcpNotPorted("integer parameter outside 64 bits")
	}
	value := number.Int64()
	if value >= math.MinInt32 && value <= math.MaxInt32 {
		return int32(value), nil
	}
	return value, nil
}

// mcpRawArgument is a value Django passes through (Regex, IExact) without
// string conversion.
func mcpRawArgument(value any) (any, error) {
	switch typed := value.(type) {
	case string:
		return typed, nil
	case bool:
		return typed, nil
	case json.Number:
		if strings.ContainsAny(string(typed), ".eE") {
			number, err := strconv.ParseFloat(string(typed), 64)
			if err != nil || math.IsNaN(number) || math.IsInf(number, 0) {
				return nil, mcpNotPorted("float parameter")
			}
			return number, nil
		}
		if number, ok := new(big.Int).SetString(string(typed), 10); ok {
			return mcpSmallInt(number)
		}
		return nil, mcpNotPorted("integer parameter")
	case mcpCoercedInt:
		return mcpSmallInt(typed.v)
	case mcpCoercedFloat:
		if math.IsNaN(typed.v) || math.IsInf(typed.v, 0) {
			return nil, mcpNotPorted("non-finite float parameter")
		}
		return typed.v, nil
	case mcpCoercedDate:
		return typed.t, nil
	case mcpCoercedDateTime:
		return typed.t, nil
	case mcpUncertain:
		return nil, mcpNotPorted("non-ASCII digits")
	}
	return nil, mcpNotPorted(fmt.Sprintf("parameter %T", value))
}

// mcpPatternText is str(value) for PatternLookup's prep_for_like_query.
func mcpPatternText(value any) (string, error) {
	switch typed := value.(type) {
	case string:
		return typed, nil
	case bool:
		return PythonStr(typed), nil
	case json.Number:
		if strings.ContainsAny(string(typed), ".eE") {
			number, err := strconv.ParseFloat(string(typed), 64)
			if err != nil || math.IsNaN(number) || math.IsInf(number, 0) {
				return "", mcpNotPorted("float parameter")
			}
			return PythonFloatStr(number), nil
		}
		return string(typed), nil
	case mcpCoercedInt:
		return typed.v.String(), nil
	case mcpCoercedFloat:
		if math.IsNaN(typed.v) || math.IsInf(typed.v, 0) {
			return "", mcpNotPorted("non-finite float parameter")
		}
		return PythonFloatStr(typed.v), nil
	case mcpCoercedDate:
		return typed.t.Format("2006-01-02"), nil
	case mcpCoercedDateTime:
		text := typed.t.Format("2006-01-02 15:04:05")
		if micro := typed.t.Nanosecond() / 1000; micro != 0 {
			text += fmt.Sprintf(".%06d", micro)
		}
		return text, nil
	case mcpUncertain:
		return "", mcpNotPorted("non-ASCII digits")
	case []any, domain.RequestObject:
		return PythonStr(typed), nil
	}
	return "", mcpNotPorted(fmt.Sprintf("pattern value %T", value))
}

// mcpUUIDValue is UUIDField.to_python: integers (and booleans) are
// uuid.UUID(int=value), strings uuid.UUID(hex=value); other values fail.
func mcpUUIDValue(value any) (string, error) {
	canonical, ok := AuditIndexUUIDValue(value)
	if !ok {
		return "", mcpValidationError(mcpMeta.Messages["UUIDField.invalid"], PythonStr(value))
	}
	return canonical, nil
}

// mcpUUID is UUIDField.to_python on a string: uuid.UUID(hex=...).
func mcpUUID(text string) (string, error) { return mcpUUIDValue(text) }

// mcpTextValue is CharField.to_python: str(value) for scalars and containers.
func mcpTextValue(value any) (string, error) {
	switch typed := value.(type) {
	case string:
		return typed, nil
	case bool:
		return PythonStr(typed), nil
	case json.Number:
		if strings.ContainsAny(string(typed), ".eE") {
			number, err := strconv.ParseFloat(string(typed), 64)
			if err != nil || math.IsInf(number, 0) {
				return "", mcpNotPorted("float text")
			}
			return PythonFloatStr(number), nil
		}
		return PythonStr(typed), nil
	case []any, domain.RequestObject:
		return PythonStr(typed), nil
	}
	return "", mcpNotPorted(fmt.Sprintf("text value %T", value))
}

func PythonRepr(value any) string {
	var out strings.Builder
	pythonRepr(&out, value)
	return out.String()
}

// mcpIntValue is int(value) inside IntegerField.get_prep_value.
func mcpIntValue(value any, name string) (*big.Int, error) {
	switch typed := value.(type) {
	case mcpCoercedInt:
		return typed.v, nil
	case bool:
		if typed {
			return big.NewInt(1), nil
		}
		return big.NewInt(0), nil
	case json.Number:
		if !strings.ContainsAny(string(typed), ".eE") {
			number, ok := new(big.Int).SetString(string(typed), 10)
			if ok {
				return number, nil
			}
		}
		number, err := strconv.ParseFloat(string(typed), 64)
		if err != nil || math.IsNaN(number) || math.IsInf(number, 0) {
			return nil, mcpNotPorted("float literal for an integer")
		}
		integer, _ := new(big.Float).SetFloat64(math.Trunc(number)).Int(nil)
		return integer, nil
	case mcpCoercedFloat:
		if math.IsNaN(typed.v) || math.IsInf(typed.v, 0) {
			return nil, mcpNotPorted("non-finite float for an integer")
		}
		integer, _ := new(big.Float).SetFloat64(math.Trunc(typed.v)).Int(nil)
		return integer, nil
	case string:
		if number, ok := PythonIntText(typed); ok {
			return number, nil
		}
		return nil, mcpRaise("Field '%s' expected a number but got %s.", name, PythonRepr(typed))
	case []any, domain.RequestObject:
		// int(list) / int(dict) are TypeErrors Django reports the same way.
		return nil, mcpRaise("Field '%s' expected a number but got %s.", name, PythonRepr(typed))
	}
	return nil, mcpNotPorted(fmt.Sprintf("integer value %T", value))
}

// mcpFloatValue is float(value) inside FloatField.get_prep_value.
func mcpFloatValue(value any, name string) (float64, error) {
	switch typed := value.(type) {
	case mcpCoercedFloat:
		if math.IsNaN(typed.v) || math.IsInf(typed.v, 0) {
			return 0, mcpNotPorted("non-finite float")
		}
		return typed.v, nil
	case json.Number:
		number, err := strconv.ParseFloat(string(typed), 64)
		if err != nil || math.IsInf(number, 0) {
			return 0, mcpNotPorted("float literal")
		}
		return number, nil
	case string:
		number, ok := PythonFloatParse(typed)
		if !ok {
			return 0, mcpRaise("Field '%s' expected a number but got %s.", name, PythonRepr(typed))
		}
		if math.IsNaN(number) || math.IsInf(number, 0) {
			return 0, mcpNotPorted("non-finite float")
		}
		return number, nil
	}
	return 0, mcpNotPorted(fmt.Sprintf("float value %T", value))
}

// mcpBoolValue is BooleanField.to_python.
func mcpBoolValue(lhs mcpExpr, value any) (mcpPrepared, error) {
	nullable := lhs.field != nil && lhs.field.Null
	invalid := func(text string) error {
		if nullable {
			return mcpValidationError(mcpMeta.Messages["BooleanField.invalid_nullable"], text)
		}
		return mcpValidationError(mcpMeta.Messages["BooleanField.invalid"], text)
	}
	switch typed := value.(type) {
	case bool:
		return mcpPrepared{param: typed}, nil
	case json.Number:
		number, err := strconv.ParseFloat(string(typed), 64)
		if err != nil {
			return mcpPrepared{}, mcpNotPorted("boolean number")
		}
		if number == 1 || number == 0 {
			return mcpPrepared{param: number == 1}, nil
		}
		return mcpPrepared{}, invalid(PythonStr(typed))
	case string:
		switch typed {
		case "t", "True", "1":
			return mcpPrepared{param: true}, nil
		case "f", "False", "0":
			return mcpPrepared{param: false}, nil
		case "":
			if nullable {
				return mcpPrepared{isNull: true}, nil
			}
		}
		return mcpPrepared{}, invalid(typed)
	case []any, domain.RequestObject:
		return mcpPrepared{}, invalid(PythonStr(typed))
	}
	return mcpPrepared{}, mcpNotPorted(fmt.Sprintf("boolean value %T", value))
}

// mcpDecimalValue is DecimalField.to_python: floats through
// Context(prec=max_digits).create_decimal_from_float, others Decimal(value).
func mcpDecimalValue(lhs mcpExpr, value any) (mcpPrepared, error) {
	digits := 0
	if lhs.field != nil {
		digits = lhs.field.MaxDigits
	}
	fromFloat := func(number float64) (mcpPrepared, error) {
		if math.IsNaN(number) {
			return mcpPrepared{}, mcpValidationError(mcpMeta.Messages["DecimalField.invalid"], "nan")
		}
		if math.IsInf(number, 0) {
			text := "inf"
			if number < 0 {
				text = "-inf"
			}
			return mcpPrepared{}, mcpValidationError(mcpMeta.Messages["DecimalField.invalid"], text)
		}
		if digits <= 0 {
			return mcpPrepared{}, mcpNotPorted("decimal from a float")
		}
		return mcpPrepared{param: new(big.Float).SetFloat64(number).Text('g', digits), cast: "::numeric"}, nil
	}
	switch typed := value.(type) {
	case mcpCoercedFloat:
		return fromFloat(typed.v)
	case bool:
		// decimal.Decimal(True) is Decimal("1").
		if typed {
			return mcpPrepared{param: "1", cast: "::numeric"}, nil
		}
		return mcpPrepared{param: "0", cast: "::numeric"}, nil
	case json.Number:
		if !strings.ContainsAny(string(typed), ".eE") {
			return mcpPrepared{param: string(typed), cast: "::numeric"}, nil
		}
		number, err := strconv.ParseFloat(string(typed), 64)
		if err != nil {
			return mcpPrepared{}, mcpNotPorted("decimal literal")
		}
		return fromFloat(number)
	case string:
		if mcpPlainDecimal.MatchString(typed) {
			return mcpPrepared{param: typed, cast: "::numeric"}, nil
		}
		if _, ok := PythonFloatParse(typed); !ok && !strings.ContainsAny(strings.ToLower(typed), "0123456789") {
			return mcpPrepared{}, mcpValidationError(mcpMeta.Messages["DecimalField.invalid"], typed)
		}
		return mcpPrepared{}, mcpNotPorted("decimal string")
	case []any, domain.RequestObject:
		return mcpPrepared{}, mcpValidationError(mcpMeta.Messages["DecimalField.invalid"], PythonStr(typed))
	}
	return mcpPrepared{}, mcpNotPorted(fmt.Sprintf("decimal value %T", value))
}

var mcpPlainDecimal = regexp.MustCompile(`^-?[0-9]+(\.[0-9]+)?$`)

var (
	mcpDateRe     = regexp.MustCompile(`^([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})$`)
	mcpDateTimeRe = regexp.MustCompile(`^([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})[T ]([0-9]{1,2}):([0-9]{1,2})` +
		`(?::([0-9]{1,2})(?:[.,]([0-9]{1,6})[0-9]{0,6})?)?[ \t\n\r\f\v]*(Z|[+-][0-9]{2}(?::?[0-9]{2})?)?$`)
	// mcpISOChars are strings date/datetime.fromisoformat could accept.
	mcpISOChars = regexp.MustCompile(`^[0-9T:+\-Z.,W ]*$`)
)

// mcpDateValue is DateField.to_python (parse_date for strings).
var (
	mcpCompactDateRe = regexp.MustCompile(`^([0-9]{4})([0-9]{2})([0-9]{2})$`)
	mcpISOWeekDateRe = regexp.MustCompile(`^([0-9]{4})-?W([0-9]{1,2})(?:-?([0-9]))?$`)
)

func mcpDateValue(value any) (string, error) {
	switch typed := value.(type) {
	case mcpCoercedDate:
		return typed.t.Format("2006-01-02"), nil
	case mcpCoercedDateTime:
		return typed.t.Format("2006-01-02"), nil
	}
	text, ok := value.(string)
	if !ok {
		return "", mcpRaise("fromisoformat: argument must be str")
	}
	if parsed, ok := PythonParseDate(text); ok {
		return parsed, nil
	}
	// parse_date raises ValueError only where its date_re matches; otherwise
	// it returns None and the field reports the generic format error.
	if mcpDateRe.MatchString(strings.TrimSuffix(text, "\n")) {
		return "", mcpValidationError(mcpMeta.Messages["DateField.invalid_date"], text)
	}
	return "", mcpValidationError(mcpMeta.Messages["DateField.invalid"], text)
}

var mcpTimeRe = regexp.MustCompile(`^([0-9]{1,2}):([0-9]{2})(?::([0-9]{2})(?:[.,]([0-9]{1,6}))?)?$`)

// mcpTimeValue is TimeField.to_python (datetime.time.fromisoformat).
func mcpTimeValue(value any) (string, error) {
	text, ok := value.(string)
	if !ok {
		return "", mcpNotPorted(fmt.Sprintf("time value %T", value))
	}
	match := mcpTimeRe.FindStringSubmatch(text)
	if match == nil {
		if mcpISOChars.MatchString(text) {
			return "", mcpNotPorted("ISO time form")
		}
		return "", mcpValidationError(mcpMeta.Messages["TimeField.invalid"], text)
	}
	hour, _ := strconv.Atoi(match[1])
	minute, _ := strconv.Atoi(match[2])
	second := 0
	if match[3] != "" {
		second, _ = strconv.Atoi(match[3])
	}
	if hour > 23 || minute > 59 || second > 59 {
		return "", mcpValidationError(mcpMeta.Messages["TimeField.invalid_time"], text)
	}
	out := fmt.Sprintf("%02d:%02d:%02d", hour, minute, second)
	if match[4] != "" {
		out += "." + (match[4] + "000000")[:6]
	}
	return out, nil
}

// mcpDateTimeValue is DateTimeField.get_prep_value: naive values are made
// aware in UTC (TIME_ZONE).
func mcpDateTimeValue(value any) (string, error) {
	switch typed := value.(type) {
	case mcpCoercedDate:
		return typed.t.Format("2006-01-02T15:04:05Z07:00"), nil
	case mcpCoercedDateTime:
		return typed.t.Format("2006-01-02T15:04:05Z07:00"), nil
	}
	text, ok := value.(string)
	if !ok {
		return "", mcpRaise("fromisoformat: argument must be str")
	}
	moment, _, outcome := DjangoParseDateTime(text)
	switch outcome {
	case DjangoDateTimeParsed:
		return moment.UTC().Format("2006-01-02T15:04:05.999999Z07:00"), nil
	case DjangoDateTimeValueError:
		return "", mcpValidationError(mcpMeta.Messages["DateTimeField.invalid_datetime"], text)
	case DjangoDateTimeNotPorted:
		return "", mcpNotPorted("datetime instant outside years 1..9999")
	}
	// parse_datetime returned None: DateTimeField.to_python then tries
	// parse_date, whose date_re distinguishes the two format errors.
	if parsed, ok := PythonParseDate(text); ok {
		return parsed + "T00:00:00Z", nil
	}
	if mcpDateRe.MatchString(strings.TrimSuffix(text, "\n")) {
		return "", mcpValidationError(mcpMeta.Messages["DateTimeField.invalid_date"], text)
	}
	return "", mcpValidationError(mcpMeta.Messages["DateTimeField.invalid"], text)
}

// mcpJSONScalar is JSONField.get_prep_value (json.dumps) for scalars.
func mcpJSONScalar(value any) (string, bool) {
	switch value.(type) {
	case string, bool, json.Number:
		return string(PythonJSONDumps(value)), true
	}
	return "", false
}

// mcpUncertain marks a string whose Python conversion uses non-ASCII digits.
type mcpUncertain struct{}

func mcpNonASCIIDigits(text string) bool {
	for _, r := range text {
		if r > 127 && unicode.IsDigit(r) {
			return true
		}
	}
	return false
}

// MCPStrptimeDate is datetime.strptime(text, '%Y-%m-%d').date() as ISO text;
// ok is false where Python raises ValueError. uncertain marks non-ASCII digits.
func MCPStrptimeDate(text string) (value string, ok, uncertain bool) {
	if mcpNonASCIIDigits(text) {
		return "", false, true
	}
	if parsed, isDate := mcpCoerceDate(text).(mcpCoercedDate); isDate && mcpStrptime[3].pattern.MatchString(text) {
		return parsed.t.Format("2006-01-02"), true, false
	}
	return "", false, false
}

// MCPUUIDArgument is UUIDField.to_python on a lookup string.
func MCPUUIDArgument(text string) (string, error) { return mcpUUID(text) }
