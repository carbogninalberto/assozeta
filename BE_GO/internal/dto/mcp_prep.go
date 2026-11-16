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

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

var (
	// errMCPUnexpectedValue: filter values are JSON scalars/containers or one
	// of the coercion results above, so no other Go type reaches preparation.
	errMCPUnexpectedValue = refusal.AppState("mcp: filter value of an unexpected type")
	// errMCPInvalidNumberToken: the JSON parser already validated the number
	// token, so strconv accepts every json.Number (only ErrRange is possible).
	errMCPInvalidNumberToken = refusal.AppState("mcp: filter number token that does not parse")
	// errMCPDecimalDigits: every DecimalField of mcp_models.json has max_digits
	// 4, 9 or 10, so a non-positive precision cannot reach
	// create_decimal_from_float.
	errMCPDecimalDigits = refusal.AppState("mcp: decimal field without max_digits")
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
	{regexp.MustCompile(`(?i)^(\p{Nd}\p{Nd}\p{Nd}\p{Nd})-(1[0-2]|0[1-9]|[1-9])-(3[01]|[12]\p{Nd}|0[1-9]|[1-9]| [1-9])T(2[0-3]|[0-1]\p{Nd}|\p{Nd}):([0-5]\p{Nd}|\p{Nd}):(6[0-1]|[0-5]\p{Nd}|\p{Nd})$`), false},
	{regexp.MustCompile(`(?i)^(\p{Nd}\p{Nd}\p{Nd}\p{Nd})-(1[0-2]|0[1-9]|[1-9])-(3[01]|[12]\p{Nd}|0[1-9]|[1-9]| [1-9])T(2[0-3]|[0-1]\p{Nd}|\p{Nd}):([0-5]\p{Nd}|\p{Nd})()$`), false},
	{regexp.MustCompile(`(?i)^(\p{Nd}\p{Nd}\p{Nd}\p{Nd})-(1[0-2]|0[1-9]|[1-9])-(3[01]|[12]\p{Nd}|0[1-9]|[1-9]| [1-9])[\t\n\v\f\r \x1c-\x1f\x{0085}\x{00a0}\x{1680}\x{2000}-\x{200a}\x{2028}\x{2029}\x{202f}\x{205f}\x{3000}]+(2[0-3]|[0-1]\p{Nd}|\p{Nd}):([0-5]\p{Nd}|\p{Nd}):(6[0-1]|[0-5]\p{Nd}|\p{Nd})$`), false},
	{regexp.MustCompile(`(?i)^(\p{Nd}\p{Nd}\p{Nd}\p{Nd})-(1[0-2]|0[1-9]|[1-9])-(3[01]|[12]\p{Nd}|0[1-9]|[1-9]| [1-9])()()()$`), true},
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
			value := 0
			for _, r := range strings.TrimSpace(match[i]) {
				digit, ok := pythonDigit(r)
				if !ok {
					continue
				}
				value = value*10 + digit
			}
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
	// mcpKind returns only the kinds above (json is handled by jsonLookup).
	return mcpPrepared{}, errMCPPreparedKind
}

// mcpPreparedArgument is the value handed to the driver: an integer that fits
// int64, or its decimal text (PostgreSQL types the literal as numeric).
func mcpPreparedArgument(prepared mcpPrepared) (any, error) {
	if prepared.integer != nil {
		if !prepared.integer.IsInt64() {
			return prepared.integer.String(), nil
		}
		return prepared.integer.Int64(), nil
	}
	return prepared.param, nil
}

// mcpNumberFloat parses a json.Number float token: an overflowing token is
// Python's inf (json.loads("1e400") == inf).
func mcpNumberFloat(text string) (float64, error) {
	number, err := strconv.ParseFloat(text, 64)
	if err != nil {
		if numErr, ok := err.(*strconv.NumError); !ok || numErr.Err != strconv.ErrRange {
			return 0, errMCPInvalidNumberToken
		}
	}
	return number, nil
}

// integerRawArgument is psycopg2's int literal type: int4, int8 or numeric.
func (q *mcpQuery) integerRawArgument(number *big.Int) string {
	if !number.IsInt64() {
		return q.args.add(number.String()) + "::numeric"
	}
	value := number.Int64()
	if value >= math.MinInt32 && value <= math.MaxInt32 {
		return q.args.add(int32(value)) + "::integer"
	}
	return q.args.add(value) + "::bigint"
}

// rawArgument is a value Django passes through (Regex, IExact) without string
// conversion: psycopg2 writes the Python value as the SQL literal its adapter
// chooses, and the complete placeholder carries that literal's type.
func (q *mcpQuery) rawArgument(value any) (string, error) {
	switch typed := value.(type) {
	case string:
		return q.args.add(typed), nil
	case []any:
		return q.mcpJSONKeyArray(typed)
	case domain.RequestObject:
		return "", mcpRaise("can't adapt type 'dict'")
	case bool:
		return q.args.add(typed) + "::boolean", nil
	case json.Number:
		if strings.ContainsAny(string(typed), ".eE") {
			number, err := mcpNumberFloat(string(typed))
			if err != nil {
				return "", err
			}
			return q.args.add(number) + "::double precision", nil
		}
		number, ok := new(big.Int).SetString(string(typed), 10)
		if !ok {
			return "", errMCPInvalidNumberToken
		}
		return q.integerRawArgument(number), nil
	case mcpCoercedInt:
		return q.integerRawArgument(typed.v), nil
	case mcpCoercedFloat:
		return q.args.add(typed.v) + "::double precision", nil
	case float64:
		return q.args.add(typed) + "::double precision", nil
	case mcpCoercedDate:
		return q.args.add(typed.t.Format("2006-01-02")) + "::date", nil
	case mcpCoercedDateTime:
		return q.args.add(typed.t.Format("2006-01-02 15:04:05")) + "::timestamp", nil
	}
	return "", errMCPUnexpectedValue
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
			number, err := mcpNumberFloat(string(typed))
			if err != nil {
				return "", err
			}
			if math.IsNaN(number) || math.IsInf(number, 0) {
				return PythonStr(number), nil
			}
			return PythonFloatStr(number), nil
		}
		return PythonStr(typed), nil
	case mcpCoercedInt:
		return typed.v.String(), nil
	case mcpCoercedFloat:
		if math.IsNaN(typed.v) || math.IsInf(typed.v, 0) {
			return PythonStr(typed.v), nil
		}
		return PythonFloatStr(typed.v), nil
	case float64:
		if math.IsNaN(typed) || math.IsInf(typed, 0) {
			return PythonStr(typed), nil
		}
		return PythonFloatStr(typed), nil
	case mcpCoercedDate:
		return typed.t.Format("2006-01-02"), nil
	case mcpCoercedDateTime:
		text := typed.t.Format("2006-01-02 15:04:05")
		if micro := typed.t.Nanosecond() / 1000; micro != 0 {
			text += fmt.Sprintf(".%06d", micro)
		}
		return text, nil
	case []any, domain.RequestObject:
		return PythonStr(typed), nil
	}
	return "", errMCPUnexpectedValue
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
			number, err := mcpNumberFloat(string(typed))
			if err != nil {
				return "", err
			}
			if math.IsNaN(number) || math.IsInf(number, 0) {
				return PythonStr(number), nil
			}
			return PythonFloatStr(number), nil
		}
		return PythonStr(typed), nil
	case float64:
		return PythonStr(typed), nil
	case []any, domain.RequestObject:
		return PythonStr(typed), nil
	}
	return "", errMCPUnexpectedValue
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
			return nil, errMCPInvalidNumberToken
		}
		number, err := mcpNumberFloat(string(typed))
		if err != nil {
			return nil, err
		}
		return mcpTruncatedInt(number, name)
	case mcpCoercedFloat:
		return mcpTruncatedInt(typed.v, name)
	case float64:
		return mcpTruncatedInt(typed, name)
	case mcpCoercedDate:
		// A date-part transform of a coerced date still prepares the value with
		// the transform's IntegerField (int(date) is a TypeError).
		return nil, mcpRaise("Field '%s' expected a number but got %s.", name, mcpPythonDateRepr(typed.t))
	case mcpCoercedDateTime:
		return nil, mcpRaise("Field '%s' expected a number but got %s.", name, mcpPythonDateTimeRepr(typed.t))
	case string:
		if number, ok := PythonIntText(typed); ok {
			return number, nil
		}
		return nil, mcpRaise("Field '%s' expected a number but got %s.", name, PythonRepr(typed))
	case []any, domain.RequestObject:
		// int(list) / int(dict) are TypeErrors Django reports the same way.
		return nil, mcpRaise("Field '%s' expected a number but got %s.", name, PythonRepr(typed))
	}
	return nil, errMCPUnexpectedValue
}

// mcpPythonDateRepr is repr(datetime.date).
func mcpPythonDateRepr(value time.Time) string {
	return fmt.Sprintf("datetime.date(%d, %d, %d)", value.Year(), int(value.Month()), value.Day())
}

// mcpPythonDateTimeRepr is repr(datetime.datetime).
func mcpPythonDateTimeRepr(value time.Time) string {
	text := fmt.Sprintf("datetime.datetime(%d, %d, %d, %d, %d", value.Year(), int(value.Month()), value.Day(),
		value.Hour(), value.Minute())
	if second, micro := value.Second(), value.Nanosecond()/1000; second != 0 || micro != 0 {
		text += fmt.Sprintf(", %d", second)
		if micro != 0 {
			text += fmt.Sprintf(", %d", micro)
		}
	}
	return text + ")"
}

// mcpTruncatedInt is int() of a Python float: ValueError for NaN (re-raised by
// get_prep_value), OverflowError for an infinity.
func mcpTruncatedInt(number float64, name string) (*big.Int, error) {
	if math.IsNaN(number) {
		return nil, mcpRaise("Field '%s' expected a number but got nan.", name)
	}
	if math.IsInf(number, 0) {
		return nil, mcpRaise("cannot convert float infinity to integer")
	}
	integer, _ := new(big.Float).SetFloat64(math.Trunc(number)).Int(nil)
	return integer, nil
}

// mcpFloatValue is float(value) inside FloatField.get_prep_value.
func mcpFloatValue(value any, name string) (float64, error) {
	switch typed := value.(type) {
	case mcpCoercedFloat:
		return typed.v, nil
	case json.Number:
		number, err := strconv.ParseFloat(string(typed), 64)
		if err != nil {
			if numErr, ok := err.(*strconv.NumError); ok && numErr.Err == strconv.ErrRange {
				if !strings.ContainsAny(string(typed), ".eE") {
					// float(int) raises OverflowError outside the float range.
					return 0, mcpRaise("int too large to convert to float")
				}
				return number, nil
			}
			return 0, errMCPInvalidNumberToken
		}
		return number, nil
	case float64:
		return typed, nil
	case bool:
		if typed {
			return 1, nil
		}
		return 0, nil
	case string:
		number, ok := PythonFloatParse(typed)
		if !ok {
			return 0, mcpRaise("Field '%s' expected a number but got %s.", name, PythonRepr(typed))
		}
		return number, nil
	case []any, domain.RequestObject:
		return 0, mcpRaise("Field '%s' expected a number but got %s.", name, PythonRepr(typed))
	}
	return 0, errMCPUnexpectedValue
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
		number, err := mcpNumberFloat(string(typed))
		if err != nil {
			return mcpPrepared{}, err
		}
		if number == 1 || number == 0 {
			return mcpPrepared{param: number == 1}, nil
		}
		return mcpPrepared{}, invalid(PythonStr(typed))
	case float64:
		if typed == 1 {
			return mcpPrepared{param: true}, nil
		}
		if typed == 0 {
			return mcpPrepared{param: false}, nil
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
	return mcpPrepared{}, errMCPUnexpectedValue
}

// mcpDecimalValue is DecimalField.to_python: floats through
// Context(prec=max_digits).create_decimal_from_float, others Decimal(value).
func mcpDecimalValue(lhs mcpExpr, value any) (mcpPrepared, error) {
	digits := 0
	if lhs.field != nil {
		digits = lhs.field.MaxDigits
	}
	fromFloat := func(number float64) (mcpPrepared, error) {
		// Only create_decimal_from_float reads max_digits: Decimal(str) and
		// Decimal(int) do not need the context.
		if digits <= 0 {
			return mcpPrepared{}, errMCPDecimalDigits
		}
		if !math.IsInf(number, 0) && !math.IsNaN(number) {
			return mcpPrepared{param: new(big.Float).SetFloat64(number).Text('g', digits), cast: "::numeric"}, nil
		}
		return mcpPrepared{}, mcpValidationError(mcpMeta.Messages["DecimalField.invalid"], mcpPythonFloatStr(number))
	}
	switch typed := value.(type) {
	case mcpCoercedFloat:
		return fromFloat(typed.v)
	case float64:
		return fromFloat(typed)
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
		number, err := mcpNumberFloat(string(typed))
		if err != nil {
			return mcpPrepared{}, err
		}
		return fromFloat(number)
	case string:
		if mcpPlainDecimal.MatchString(typed) {
			return mcpPrepared{param: typed, cast: "::numeric"}, nil
		}
		text, ok := mcpDecimalString(typed)
		if !ok {
			return mcpPrepared{}, mcpValidationError(mcpMeta.Messages["DecimalField.invalid"], typed)
		}
		return mcpPrepared{param: text, cast: "::numeric"}, nil
	case []any, domain.RequestObject:
		return mcpPrepared{}, mcpValidationError(mcpMeta.Messages["DecimalField.invalid"], PythonStr(typed))
	}
	return mcpPrepared{}, errMCPUnexpectedValue
}

// mcpPlainDecimal is the common Decimal(str) form, passed through unchanged.
var mcpPlainDecimal = regexp.MustCompile(`^-?[0-9]+(\.[0-9]+)?$`)

// mcpPythonFloatStr is str(float); PythonFloatStr writes "Infinity".
func mcpPythonFloatStr(value float64) string {
	switch {
	case math.IsInf(value, 1):
		return "inf"
	case math.IsInf(value, -1):
		return "-inf"
	case math.IsNaN(value):
		return "nan"
	}
	return PythonFloatStr(value)
}

// The C decimal module's construction range (MIN_ETINY and MAX_EMAX).
var (
	mcpDecimalMinAdjusted = big.NewInt(-1999999999999999997)
	mcpDecimalMaxAdjusted = big.NewInt(999999999999999999)
)

// mcpDecimalString is Decimal(str) of the C _decimal module: Unicode decimal
// digits and surrounding whitespace are folded to ASCII, underscores are
// ignored, and a finite value comes back as numeric text PostgreSQL reads as
// the same number.
func mcpDecimalString(text string) (string, bool) {
	var folded strings.Builder
	for _, r := range PythonStrip(text) {
		if r == '_' {
			continue
		}
		if digit, ok := pythonDigit(r); ok {
			folded.WriteByte(byte('0' + digit))
			continue
		}
		if r > 127 {
			return "", false
		}
		folded.WriteRune(r)
	}
	s := folded.String()
	sign := ""
	if s != "" && (s[0] == '+' || s[0] == '-') {
		if s[0] == '-' {
			sign = "-"
		}
		s = s[1:]
	}
	lower := strings.ToLower(s)
	if lower == "inf" || lower == "infinity" || mcpDecimalNaN(lower) {
		// A valid but non-finite Decimal: DecimalField.to_python rejects it.
		return "", false
	}
	mantissa, exponent := s, ""
	hasExponent := false
	if index := strings.IndexAny(s, "eE"); index >= 0 {
		hasExponent = true
		mantissa, exponent = s[:index], s[index+1:]
		if strings.ContainsAny(exponent, "eE") {
			return "", false
		}
	}
	exponentSign := 1
	exponentDigits := exponent
	if exponentDigits != "" && (exponentDigits[0] == '+' || exponentDigits[0] == '-') {
		if exponentDigits[0] == '-' {
			exponentSign = -1
		}
		exponentDigits = exponentDigits[1:]
	}
	if hasExponent && !mcpASCIIDigits(exponentDigits) {
		return "", false
	}
	integerPart, fractionPart := mantissa, ""
	if index := strings.IndexByte(mantissa, '.'); index >= 0 {
		integerPart, fractionPart = mantissa[:index], mantissa[index+1:]
		if strings.ContainsRune(fractionPart, '.') {
			return "", false
		}
	}
	if !mcpASCIIDigitsOrEmpty(integerPart) || !mcpASCIIDigitsOrEmpty(fractionPart) {
		return "", false
	}
	if integerPart == "" && fractionPart == "" {
		return "", false
	}
	exponentValue := new(big.Int)
	if hasExponent {
		exponentValue.SetString(exponentDigits, 10)
		if exponentSign < 0 {
			exponentValue.Neg(exponentValue)
		}
	}
	// The C parser accepts a value whose adjusted exponent (exponent plus the
	// coefficient's digit count minus one) stays inside MIN_ETINY..MAX_EMAX.
	significant := strings.TrimLeft(integerPart+fractionPart, "0")
	if significant == "" {
		significant = "0"
	}
	adjusted := new(big.Int).Set(exponentValue)
	adjusted.Sub(adjusted, big.NewInt(int64(len(fractionPart))))
	adjusted.Add(adjusted, big.NewInt(int64(len(significant)-1)))
	if adjusted.Cmp(mcpDecimalMinAdjusted) < 0 || adjusted.Cmp(mcpDecimalMaxAdjusted) > 0 {
		return "", false
	}
	integerOut := strings.TrimLeft(integerPart, "0")
	if integerOut == "" {
		integerOut = "0"
	}
	out := sign + integerOut
	if fractionPart != "" {
		out += "." + fractionPart
	}
	if hasExponent {
		mark := ""
		if exponentSign < 0 {
			mark = "-"
		}
		digitsOut := strings.TrimLeft(exponentDigits, "0")
		if digitsOut == "" {
			digitsOut = "0"
		}
		out += "e" + mark + digitsOut
	}
	return out, true
}

// mcpDecimalNaN reports the nan / snan forms (with an optional digit payload).
func mcpDecimalNaN(text string) bool {
	for _, prefix := range []string{"nan", "snan"} {
		if strings.HasPrefix(text, prefix) {
			return mcpASCIIDigitsOrEmpty(text[len(prefix):])
		}
	}
	return false
}

func mcpASCIIDigits(text string) bool {
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

func mcpASCIIDigitsOrEmpty(text string) bool {
	for i := 0; i < len(text); i++ {
		if text[i] < '0' || text[i] > '9' {
			return false
		}
	}
	return true
}

var (
	mcpDateRe     = regexp.MustCompile(`^([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})$`)
	mcpDateTimeRe = regexp.MustCompile(`^([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})[T ]([0-9]{1,2}):([0-9]{1,2})` +
		`(?::([0-9]{1,2})(?:[.,]([0-9]{1,6})[0-9]{0,6})?)?[ \t\n\r\f\v]*(Z|[+-][0-9]{2}(?::?[0-9]{2})?)?$`)
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

// mcpTimeRe is Django's time_re: re.match, Unicode \d and $ before one final
// newline.
var mcpTimeRe = regexp.MustCompile(`^(\p{Nd}{1,2}):(\p{Nd}{1,2})(?::(\p{Nd}{1,2})(?:[.,](\p{Nd}{1,6})\p{Nd}{0,6})?)?\n?$`)

// mcpTimeValue is TimeField.to_python: datetime.time.fromisoformat, then
// parse_time's time_re fallback.
func mcpTimeValue(value any) (string, error) {
	switch typed := value.(type) {
	case mcpCoercedDateTime:
		return typed.t.Format("15:04:05"), nil
	case string:
		if parsed, ok := mcpISOTime(typed); ok {
			return parsed, nil
		}
		match := mcpTimeRe.FindStringSubmatch(typed)
		if match == nil {
			return "", mcpValidationError(mcpMeta.Messages["TimeField.invalid"], typed)
		}
		hour, minute, second := mcpDecimalDigits(match[1]), mcpDecimalDigits(match[2]), mcpDecimalDigits(match[3])
		if hour > 23 || minute > 59 || second > 59 {
			return "", mcpValidationError(mcpMeta.Messages["TimeField.invalid_time"], typed)
		}
		out := fmt.Sprintf("%02d:%02d:%02d", hour, minute, second)
		if match[4] != "" {
			micro := mcpDecimalDigits(match[4])
			for i := len([]rune(match[4])); i < 6; i++ {
				micro *= 10
			}
			out += fmt.Sprintf(".%06d", micro)
		}
		return out, nil
	}
	// parse_time calls time.fromisoformat with a non-str: TypeError propagates.
	return "", mcpRaise("fromisoformat: argument must be str")
}

// mcpDecimalDigits is int() of a run of Unicode decimal digits.
func mcpDecimalDigits(text string) int {
	value := 0
	for _, r := range text {
		if digit, ok := pythonDigit(r); ok {
			value = value*10 + digit
		}
	}
	return value
}

// mcpISOByte reads text[i], with the C reader's terminating NUL past the end.
func mcpISOByte(text string, i int) byte {
	if i < len(text) {
		return text[i]
	}
	return 0
}

// mcpISODigits is CPython's parse_digits: exactly n ASCII digits at pos.
func mcpISODigits(text string, pos, n int) (int, int, bool) {
	value := 0
	for i := 0; i < n; i++ {
		c := mcpISOByte(text, pos+i)
		if c < '0' || c > '9' {
			return 0, pos, false
		}
		value = value*10 + int(c-'0')
	}
	return value, pos + n, true
}

// mcpISOClock is CPython's parse_hh_mm_ss_ff over text[from:end]: the buffer
// continues past end. more is return code 1 (something other than NUL follows).
func mcpISOClock(text string, from, end int) (parts [4]int, more, ok bool) {
	pos, hasSep := from, true
	for i := 0; i < 3; i++ {
		value, next, valid := mcpISODigits(text, pos, 2)
		if !valid {
			return parts, false, false
		}
		parts[i], pos = value, next
		c := mcpISOByte(text, pos)
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
	value, next, valid := mcpISODigits(text, pos, take)
	if !valid {
		return parts, false, false
	}
	for i := take; i < 6; i++ {
		value *= 10
	}
	parts[3], pos = value, next
	for c := mcpISOByte(text, pos); c >= '0' && c <= '9'; c = mcpISOByte(text, pos) {
		pos++
	}
	return parts, mcpISOByte(text, pos) != 0, true
}

// mcpISOTime is datetime.time.fromisoformat (Python 3.13) with the tzinfo
// dropped; ok is false for its ValueError.
func mcpISOTime(text string) (string, bool) {
	if strings.HasPrefix(text, "T") {
		text = text[1:]
	}
	if len(text) < 2 {
		return "", false
	}
	zone := 0
	for zone < len(text) && text[zone] != 'Z' && text[zone] != '+' && text[zone] != '-' {
		zone++
	}
	parts, more, ok := mcpISOClock(text, 0, zone)
	if !ok {
		return "", false
	}
	if zone == len(text) {
		if more {
			return "", false
		}
	} else if text[zone] == 'Z' {
		if zone+1 != len(text) {
			return "", false
		}
	} else {
		offset, offsetMore, offsetOK := mcpISOClock(text, zone+1, len(text))
		if !offsetOK || offsetMore {
			return "", false
		}
		micro := int64(((offset[0]*60+offset[1])*60+offset[2]))*1000000 + int64(offset[3])
		if micro >= 24*3600*1000000 {
			return "", false
		}
	}
	hour, minute, second, micro := parts[0], parts[1], parts[2], parts[3]
	if hour > 23 || minute > 59 || second > 59 {
		return "", false
	}
	out := fmt.Sprintf("%02d:%02d:%02d", hour, minute, second)
	if micro != 0 {
		out += fmt.Sprintf(".%06d", micro)
	}
	return out, true
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
	moment, _, outcome := DjangoParseDateTimeWide(text)
	switch outcome {
	case DjangoDateTimeParsed:
		if !DjangoDateTimeOutside(moment) {
			return moment.UTC().Format("2006-01-02T15:04:05.999999Z07:00"), nil
		}
		// Python keeps the aware value and psycopg2 sends its wall clock with
		// its own offset, which timestamptz accepts outside datetime's range.
		wall := DjangoDateTimeWall(text, moment)
		return mcpWallDateTime(wall, wall.Sub(moment)), nil
	case DjangoDateTimeValueError:
		return "", mcpValidationError(mcpMeta.Messages["DateTimeField.invalid_datetime"], text)
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

// mcpWallDateTime is a Python datetime's isoformat: wall clock plus its own
// offset (seconds included when the tzinfo carries them).
func mcpWallDateTime(wall time.Time, offset time.Duration) string {
	text := wall.Format("2006-01-02T15:04:05")
	if micro := wall.Nanosecond() / 1000; micro != 0 {
		text += fmt.Sprintf(".%06d", micro)
	}
	sign := "+"
	if offset < 0 {
		sign, offset = "-", -offset
	}
	hours := int(offset / time.Hour)
	minutes := int(offset/time.Minute) % 60
	seconds := int(offset/time.Second) % 60
	text += fmt.Sprintf("%s%02d:%02d", sign, hours, minutes)
	if seconds != 0 {
		text += fmt.Sprintf(":%02d", seconds)
	}
	return text
}

// MCPStrptimeDate is datetime.strptime(text, '%Y-%m-%d').date() as ISO text;
// ok is false where Python raises ValueError. uncertain is always false (the
// non-ASCII digit forms are supported now); the signature has another caller.
func MCPStrptimeDate(text string) (value string, ok, uncertain bool) {
	if parsed, isDate := mcpCoerceDate(text).(mcpCoercedDate); isDate && mcpStrptime[3].pattern.MatchString(text) {
		return parsed.t.Format("2006-01-02"), true, false
	}
	return "", false, false
}

// MCPUUIDArgument is UUIDField.to_python on a lookup string.
func MCPUUIDArgument(text string) (string, error) { return mcpUUID(text) }
