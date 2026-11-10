package dto

import (
	"encoding/json"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// Validation codes of the course write serializers (rendered by the handler).
const (
	CourseWritesMaxValue = "max-value:"
	// CourseWritesDate is DateField's format error for DATE_INPUT_FORMATS ['%d/%m/%Y'].
	CourseWritesDate = "date-dmy"
)

// PythonIterate is what `for item in value` yields for a parsed request
// value: list items, dict keys, string characters; ok is false for values
// that are not iterable (None, numbers, booleans).
func PythonIterate(value any) ([]any, bool) {
	switch v := value.(type) {
	case []any:
		return v, true
	case domain.RequestObject:
		keys := make([]any, len(v))
		for index, field := range v {
			keys[index] = field.Name
		}
		return keys, true
	case string:
		points := pythonCodePoints(v)
		items := make([]any, len(points))
		for index, point := range points {
			items[index] = string(point)
		}
		return items, true
	}
	return nil, false
}

// CourseEvent is CourseEventsSerializer's validated data.
type CourseEvent struct {
	ID     int
	Amount string
	Date   time.Time
	// InstallmentID is the validated course_subscription_installment_id of
	// an event that carries the key (nil without it).
	InstallmentID *string
}

// CourseEventUnique is the UniqueValidator code of an event's
// course_subscription_installment_id.
const CourseEventUnique = "unique:course subscription installment:course subscription installment id"

// CourseEventTaken is the UniqueValidator lookup of an event's
// course_subscription_installment_id: the default manager's
// filter(pk=id).exists().
type CourseEventTaken func(id string) (bool, error)

var (
	// strptime('%d/%m/%Y'): %d and %m accept one or two digits (%d also a
	// space and a digit), %Y four digits.
	courseWritesDMY   = regexp.MustCompile(`^(3[01]|[12]\d|0[1-9]|[1-9]| [1-9])/(1[0-2]|0[1-9]|[1-9])/(\d{4})$`)
	courseWritesDigit = regexp.MustCompile(`\d`)
	// courseWritesDMYAny is the same pattern with Python's \d: the day's
	// second digit and the year accept decimal digits of any script.
	courseWritesDMYAny = regexp.MustCompile(`^(3[01]|[12]\p{Nd}|0[1-9]|[1-9]| [1-9])/(1[0-2]|0[1-9]|[1-9])/(\p{Nd}{4})$`)
)

// ParseDMYDate is datetime.strptime(value, '%d/%m/%Y').date(); ok is false
// on a format or calendar error. int() reads the decimal digits of other
// scripts the pattern accepts.
func ParseDMYDate(value any) (time.Time, bool, error) {
	text, ok := value.(string)
	if !ok {
		return time.Time{}, false, nil
	}
	if !courseWritesDMYAny.MatchString(text) {
		return time.Time{}, false, nil
	}
	match := courseWritesDMY.FindStringSubmatch(PythonASCIIDigits(text))
	if match == nil {
		return time.Time{}, false, nil
	}
	day, _ := strconv.Atoi(strings.TrimSpace(match[1]))
	month, _ := strconv.Atoi(match[2])
	year, _ := strconv.Atoi(match[3])
	date := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
	if year < 1 || date.Day() != day || int(date.Month()) != month {
		return time.Time{}, false, nil
	}
	return date, true, nil
}

func courseWritesInteger(value any, minimum, maximum int64) (int64, string) {
	number, code := DRFInteger(value)
	if code != "" {
		return 0, code
	}
	if number.Cmp(big.NewInt(maximum)) > 0 {
		return 0, CourseWritesMaxValue + strconv.FormatInt(maximum, 10)
	}
	if number.Cmp(big.NewInt(minimum)) < 0 {
		return 0, "min-value:" + strconv.FormatInt(minimum, 10)
	}
	return number.Int64(), ""
}

// courseWritesDecimal is DecimalField(max_digits=9, decimal_places=2,
// min_value=0.00).
func courseWritesDecimal(value any) (string, string) {
	text, code := DRFDecimal(value, 9, 2)
	if code != "" {
		return "", code
	}
	if strings.HasPrefix(text, "-") && strings.Trim(text, "-0.") != "" {
		return "", "min-value:0.0"
	}
	return text, ""
}

var (
	drfTrue  = map[string]bool{"t": true, "T": true, "y": true, "Y": true, "yes": true, "Yes": true, "YES": true, "true": true, "True": true, "TRUE": true, "on": true, "On": true, "ON": true, "1": true}
	drfFalse = map[string]bool{"f": true, "F": true, "n": true, "N": true, "no": true, "No": true, "NO": true, "false": true, "False": true, "FALSE": true, "off": true, "Off": true, "OFF": true, "0": true}
)

// DRFBoolean is BooleanField.to_internal_value (None is handled by the caller).
func DRFBoolean(value any) (bool, bool) {
	switch v := value.(type) {
	case bool:
		return v, true
	case string:
		if drfTrue[v] {
			return true, true
		}
		if drfFalse[v] {
			return false, true
		}
	case json.Number:
		number, err := strconv.ParseFloat(string(v), 64)
		if err == nil && (number == 0 || number == 1) {
			return number == 1, true
		}
	}
	return false, false
}

// ValidateCourseEvent is CourseEventsSerializer(data=value).is_valid(): the
// errors are DRF's field codes. An event carrying the writable primary key
// (UniqueValidator) is not ported.
func ValidateCourseEvent(value any, taken CourseEventTaken) (CourseEvent, map[string][]string, error) {
	object, ok := value.(domain.RequestObject)
	if !ok {
		if value == nil {
			return CourseEvent{}, map[string][]string{"non_field_errors": {PaymentCategoryValidationNull}}, nil
		}
		return CourseEvent{}, map[string][]string{"non_field_errors": {"dictionary:" + pythonTypeName(value)}}, nil
	}
	errors := map[string][]string{}
	var event CourseEvent
	// The writable primary key: an optional UUIDField with a UniqueValidator.
	if raw, present := object.Lookup("course_subscription_installment_id"); present {
		const name = "course_subscription_installment_id"
		id, valid := DRFUUIDValue(raw)
		switch {
		case raw == nil:
			errors[name] = []string{PaymentCategoryValidationNull}
		case !valid:
			errors[name] = []string{PaymentCategoryValidationUUID}
		case taken == nil:
			return CourseEvent{}, nil, domain.ErrCourseWritesNotPorted
		default:
			exists, err := taken(id)
			if err != nil {
				return CourseEvent{}, nil, err
			}
			if exists {
				errors[name] = []string{CourseEventUnique}
			} else {
				event.InstallmentID = &id
			}
		}
	}
	field := func(name string, parse func(any) string) {
		raw, present := object.Lookup(name)
		switch {
		case !present:
			errors[name] = []string{PaymentCategoryValidationRequired}
		case raw == nil:
			errors[name] = []string{PaymentCategoryValidationNull}
		default:
			if code := parse(raw); code != "" {
				errors[name] = []string{code}
			}
		}
	}
	field("id", func(raw any) string {
		number, code := courseWritesInteger(raw, 0, 30)
		event.ID = int(number)
		return code
	})
	field("amount", func(raw any) string {
		amount, code := courseWritesDecimal(raw)
		event.Amount = amount
		return code
	})
	var notPorted error
	field("payment_date", func(raw any) string {
		date, valid, err := ParseDMYDate(raw)
		if err != nil {
			notPorted = err
		}
		if !valid {
			return CourseWritesDate
		}
		event.Date = date
		return ""
	})
	if notPorted != nil {
		return CourseEvent{}, nil, notPorted
	}
	if len(errors) > 0 {
		return CourseEvent{}, errors, nil
	}
	return event, nil, nil
}

// CourseAddInput is CourseAddSerializer's validated data. Unset optional
// model fields keep the model defaults; FeeDefault and OneFeeDefault mark the
// serializer's float 0.00 defaults.
type CourseAddInput struct {
	TitleSet, DescriptionSet  bool
	Title, Description        *string
	Fee, OneFee               string
	FeeDefault, OneFeeDefault bool
	OneFeePayment, Multi      bool
	// Events and MultipleQuotes are the raw JSON values (nil: None).
	Events, MultipleQuotes any
	CourseType             int
	SportSeason            bool
	BilledFrequency        *int
	FromSubscription       bool
	BilledDayOfMonth       int
	AutoRenewal            bool
	// EventItems are the validated events (multi_payments with events).
	EventItems []CourseEvent
}

// CourseAddInvalid carries the 400 bodies of CourseAddSerializer.is_valid():
// Fields are DRF's field errors; Events is a custom events message
// ({'msg:': ..., 'details': {'events': [...]}}); Event is a nested
// CourseEventsSerializer error raised as is.
type CourseAddInvalid struct {
	Fields map[string][]string
	Events string
	Event  map[string][]string
}

func (e *CourseAddInvalid) Error() string { return "invalid course add input" }

// pythonRational is Decimal(value) of a raw event amount already accepted by
// DecimalField: strings and integers exactly; floats only when their binary
// value has at most two decimal places (other floats are not ported).
func pythonRational(value any) (*big.Rat, error) {
	switch v := value.(type) {
	case string:
		text := strings.ReplaceAll(strings.TrimFunc(v, unicode.IsSpace), "_", "")
		number, ok := new(big.Rat).SetString(text)
		if !ok {
			return nil, domain.ErrCourseWritesNotPorted
		}
		return number, nil
	case json.Number:
		if !strings.ContainsAny(string(v), ".eE") {
			number, ok := new(big.Rat).SetString(string(v))
			if !ok {
				return nil, domain.ErrCourseWritesNotPorted
			}
			return number, nil
		}
		float, err := strconv.ParseFloat(string(v), 64)
		if err != nil {
			return nil, domain.ErrCourseWritesNotPorted
		}
		number := new(big.Rat)
		if number.SetFloat64(float) == nil {
			return nil, domain.ErrCourseWritesNotPorted
		}
		scaled := new(big.Rat).Mul(number, big.NewRat(100, 1))
		if !scaled.IsInt() {
			return nil, domain.ErrCourseWritesNotPorted
		}
		return number, nil
	}
	return nil, domain.ErrCourseWritesNotPorted
}

// ValidateCourseAdd is CourseAddSerializer(data=value).is_valid(raise_exception=True)
// with its overridden events checks.
func ValidateCourseAdd(value any, taken CourseEventTaken) (CourseAddInput, error) {
	input := CourseAddInput{CourseType: 1, BilledDayOfMonth: 1}
	one := 1
	input.BilledFrequency = &one
	object, ok := value.(domain.RequestObject)
	if !ok {
		code := "dictionary:" + pythonTypeName(value)
		if value == nil {
			code = PaymentCategoryValidationNull
		}
		return input, &CourseAddInvalid{Fields: map[string][]string{"non_field_errors": {code}}}
	}
	errors := map[string][]string{}
	optional := func(name string, null bool, parse func(any) string) {
		raw, present := object.Lookup(name)
		if !present {
			return
		}
		if raw == nil && !null {
			errors[name] = []string{PaymentCategoryValidationNull}
			return
		}
		if code := parse(raw); code != "" {
			errors[name] = []string{code}
		}
	}
	text := func(target **string, set *bool, maximum int) func(any) string {
		return func(raw any) string {
			*set = true
			value, codes := modelTextValue(SupplierField{Null: true, Max: maximum}, raw, false)
			if len(codes) > 0 {
				return codes[0]
			}
			*target = value
			return ""
		}
	}
	decimal := func(target *string, defaulted *bool) func(any) string {
		return func(raw any) string {
			value, code := courseWritesDecimal(raw)
			*target, *defaulted = value, false
			return code
		}
	}
	boolean := func(target *bool) func(any) string {
		return func(raw any) string {
			value, valid := DRFBoolean(raw)
			if !valid {
				return PaymentCategoryValidationBoolean
			}
			*target = value
			return ""
		}
	}
	input.FeeDefault, input.OneFeeDefault, input.Fee, input.OneFee = true, true, "0.00", "0.00"
	optional("title", true, text(&input.Title, &input.TitleSet, 255))
	optional("description", true, text(&input.Description, &input.DescriptionSet, 0))
	optional("fee", false, decimal(&input.Fee, &input.FeeDefault))
	optional("one_fee", false, decimal(&input.OneFee, &input.OneFeeDefault))
	optional("one_fee_payment", false, boolean(&input.OneFeePayment))
	optional("multi_payments_split", false, boolean(&input.Multi))
	optional("events", false, func(raw any) string { input.Events = raw; return "" })
	optional("multiple_quotes", true, func(raw any) string { input.MultipleQuotes = raw; return "" })
	optional("course_type", false, func(raw any) string {
		choice := PythonStr(raw)
		switch choice {
		case "1", "2", "3":
			input.CourseType, _ = strconv.Atoi(choice)
			return ""
		}
		return "choice:" + choice
	})
	optional("billed_duration_is_sport_season", false, boolean(&input.SportSeason))
	optional("billed_frequency", true, func(raw any) string {
		if raw == nil {
			input.BilledFrequency = nil
			return ""
		}
		number, code := courseWritesInteger(raw, 0, 32767)
		value := int(number)
		input.BilledFrequency = &value
		return code
	})
	optional("billed_from_subscription_date", false, boolean(&input.FromSubscription))
	optional("billed_from_day_of_month", false, func(raw any) string {
		number, code := courseWritesInteger(raw, 1, 28)
		input.BilledDayOfMonth = int(number)
		return code
	})
	optional("auto_renewal", false, boolean(&input.AutoRenewal))
	if len(errors) > 0 {
		return input, &CourseAddInvalid{Fields: errors}
	}
	return input, validateCourseAddEvents(&input, taken)
}

func validateCourseAddEvents(input *CourseAddInput, taken CourseEventTaken) error {
	present := PythonTruthy(input.Events)
	switch {
	case input.Multi && !present:
		return &CourseAddInvalid{Events: "This field is required."}
	case !input.Multi && present:
		return &CourseAddInvalid{Events: "This field is not allowed."}
	case !input.Multi:
		return nil
	}
	items, ok := PythonIterate(input.Events)
	if !ok {
		// len() of a number: TypeError.
		return domain.ErrCourseWritesServer
	}
	if len(items) > 30 {
		return &CourseAddInvalid{Events: "The maximum number of events is 30."}
	}
	if len(items) < 2 {
		return &CourseAddInvalid{Events: "The minimum number of events is 2."}
	}
	fee, ok := new(big.Rat).SetString(input.Fee)
	if !ok {
		return domain.ErrCourseWritesServer
	}
	total := new(big.Rat)
	for _, item := range items {
		event, invalid, err := ValidateCourseEvent(item, taken)
		if err != nil {
			return err
		}
		if invalid != nil {
			return &CourseAddInvalid{Event: invalid}
		}
		raw, _ := item.(domain.RequestObject).Lookup("amount")
		amount, err := pythonRational(raw)
		if err != nil {
			return err
		}
		if amount.Cmp(fee) > 0 {
			return &CourseAddInvalid{Events: "The amount of the event cannot be greater than the course fee."}
		}
		total.Add(total, amount)
		input.EventItems = append(input.EventItems, event)
	}
	if total.Cmp(fee) != 0 {
		return &CourseAddInvalid{Events: "The sum of the events amount must be equal to the course fee."}
	}
	return nil
}

// --- course_update raw assignments -------------------------------------------

var (
	courseWritesDecimalText = regexp.MustCompile(`^-?\d{1,7}(\.\d{1,2})?$`)
	courseWritesDateOnly    = regexp.MustCompile(`^(\d{4})-(\d{2})-(\d{2})$`)
	courseWritesDateTime    = regexp.MustCompile(`^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{3}|\d{6}))?)?(Z|[+-]\d{2}:\d{2})?$`)
)

// CourseUpdateDecimal is a raw fee value assigned to a DecimalField(9, 2):
// the stored text and str() of the value. Only plain decimals of up to seven
// whole digits and two places are ported.
func CourseUpdateDecimal(value any) (stored, audit string, ok bool) {
	var text string
	switch v := value.(type) {
	case string:
		text, audit = v, v
	case json.Number:
		text, audit = string(v), PythonStr(v)
	default:
		return "", "", false
	}
	if !courseWritesDecimalText.MatchString(text) {
		return "", "", false
	}
	quantized, code := DRFDecimal(text, 9, 2)
	if code != "" || strings.HasPrefix(quantized, "-") && strings.Trim(quantized, "-0.") == "" {
		return "", "", false
	}
	return quantized, audit, true
}

// CourseUpdateBoolean is a raw value assigned to a BooleanField: the stored
// value and str() of it (BooleanField.to_python's accepted values).
func CourseUpdateBoolean(value any) (bool, string, bool) {
	switch v := value.(type) {
	case bool:
		return v, PythonStr(v), true
	case string:
		switch v {
		case "t", "True", "1":
			return true, v, true
		case "f", "False", "0":
			return false, v, true
		}
	case json.Number:
		switch string(v) {
		case "1":
			return true, "1", true
		case "0":
			return false, "0", true
		}
	}
	return false, "", false
}

// CourseUpdateInteger is a raw JSON integer assigned to an integer field.
func CourseUpdateInteger(value any, minimum, maximum int64) (int, bool) {
	number, ok := value.(json.Number)
	if !ok || strings.ContainsAny(string(number), ".eE") {
		return 0, false
	}
	parsed, err := strconv.ParseInt(string(number), 10, 64)
	if err != nil || parsed < minimum || parsed > maximum {
		return 0, false
	}
	return int(parsed), true
}

// CourseUpdateDateTime is a raw string assigned to a DateTimeField: ISO dates
// and date-times (naive values are UTC). valid is false for a calendar error
// (Django's to_python raises at save); ok is false for formats not ported.
func CourseUpdateDateTime(value string) (moment time.Time, valid, ok bool) {
	if match := courseWritesDateOnly.FindStringSubmatch(value); match != nil {
		date, valid := courseWritesCalendar(match[1], match[2], match[3], "0", "0", "0", "")
		return date, valid, true
	}
	match := courseWritesDateTime.FindStringSubmatch(value)
	if match == nil {
		return time.Time{}, false, false
	}
	moment, valid = courseWritesCalendar(match[1], match[2], match[3], match[4], match[5], match[6], match[7])
	if !valid {
		return time.Time{}, false, true
	}
	if zone := match[8]; zone != "" && zone != "Z" {
		hours, _ := strconv.Atoi(zone[1:3])
		minutes, _ := strconv.Atoi(zone[4:6])
		if hours > 23 || minutes > 59 {
			return time.Time{}, false, false
		}
		offset := time.Duration(hours)*time.Hour + time.Duration(minutes)*time.Minute
		if zone[0] == '+' {
			moment = moment.Add(-offset)
		} else {
			moment = moment.Add(offset)
		}
	}
	return moment, true, true
}

func courseWritesCalendar(year, month, day, hour, minute, second, fraction string) (time.Time, bool) {
	number := func(text string) int {
		if text == "" {
			return 0
		}
		value, _ := strconv.Atoi(text)
		return value
	}
	y, mo, d, h, mi, s := number(year), number(month), number(day), number(hour), number(minute), number(second)
	micro := 0
	if fraction != "" {
		micro = number((fraction + "000000")[:6])
	}
	if y < 1 || mo < 1 || mo > 12 || h > 23 || mi > 59 || s > 59 {
		return time.Time{}, false
	}
	moment := time.Date(y, time.Month(mo), d, h, mi, s, micro*1000, time.UTC)
	if moment.Day() != d || int(moment.Month()) != mo {
		return time.Time{}, false
	}
	return moment, true
}

// CourseWritesNaiveUTC is str() of the naive UTC datetime auditlog compares.
func CourseWritesNaiveUTC(value time.Time) string {
	value = value.UTC()
	if value.Nanosecond()/1000 == 0 {
		return value.Format("2006-01-02 15:04:05")
	}
	return value.Format("2006-01-02 15:04:05.000000")
}

// CourseWritesEscape is the views' .replace("<", "&lt;").replace(">", "&gt;").
func CourseWritesEscape(text string) string {
	return strings.ReplaceAll(strings.ReplaceAll(text, "<", "&lt;"), ">", "&gt;")
}

// CourseWritesStoredJSON parses stored JSON text (nil: None).
func CourseWritesStoredJSON(text *string) (any, error) {
	if text == nil {
		return nil, nil
	}
	return ParseReferenceJSON([]byte(*text))
}

// CourseWritesAuditJSON is auditlog's JSONField rendering: json.dumps(value, sort_keys=True).
func CourseWritesAuditJSON(value any) string { return string(PythonJSONDumpsSorted(value)) }

// CourseWritesJSONText is the stored JSON text of a value (nil for None).
func CourseWritesJSONText(value any) *string {
	if value == nil {
		return nil
	}
	text := string(PythonJSONDumps(value))
	return &text
}
