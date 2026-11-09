package importeffects

import (
	"encoding/json"
	"math"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// Kind is the Python type of an in-memory model attribute.
type Kind int

const (
	None     Kind = iota
	Str           // str
	Int           // int (S holds the decimal text)
	Bool          // bool
	Decimal       // decimal.Decimal (S holds str(value))
	UUID          // uuid.UUID (S holds the canonical text)
	DateTime      // aware datetime.datetime (T)
	Date          // datetime.date (S holds isoformat())
	Float         // float (S holds repr(value))
	JSON          // a decoded JSON container (J: domain.RequestObject or []any)
)

// Value is one in-memory model attribute: what Django's field would hold
// after Model.__init__ (and the importer's _parse_value).
type Value struct {
	Kind Kind
	S    string
	B    bool
	T    time.Time
	J    any
}

// NoneValue is Python's None.
var NoneValue = Value{}

// StrValue is a str.
func StrValue(text string) Value { return Value{Kind: Str, S: text} }

// UUIDValue is a uuid.UUID from its canonical text.
func UUIDValue(text string) Value { return Value{Kind: UUID, S: text} }

// IntValue is an int.
func IntValue(n int64) Value { return Value{Kind: Int, S: strconv.FormatInt(n, 10)} }

// DateTimeValue is an aware datetime.
func DateTimeValue(t time.Time) Value { return Value{Kind: DateTime, T: t} }

// BoolValue is a bool.
func BoolValue(b bool) Value { return Value{Kind: Bool, B: b} }

// IsNone reports Python None.
func (v Value) IsNone() bool { return v.Kind == None }

var canonicalUUID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

// CanonicalUUID reports text equal to str(uuid.UUID(text)).
func CanonicalUUID(text string) bool { return canonicalUUID.MatchString(text) }

// FromJSON is a decoded JSON value as Python's json module returns it
// (dto.ParseProfileTablesJSON's representation: json.Number for numbers,
// domain.RequestObject for objects).
func FromJSON(raw any) (Value, error) {
	switch v := raw.(type) {
	case nil:
		return NoneValue, nil
	case string:
		return StrValue(v), nil
	case bool:
		return BoolValue(v), nil
	case json.Number:
		if !strings.ContainsAny(string(v), ".eE") {
			n, ok := new(big.Int).SetString(string(v), 10)
			if !ok || len(strings.TrimPrefix(string(v), "-")) > 4300 {
				return Value{}, unsupported("integer literal")
			}
			return Value{Kind: Int, S: n.String()}, nil
		}
		f, err := strconv.ParseFloat(string(v), 64)
		if err != nil || math.IsInf(f, 0) {
			return Value{}, unsupported("float literal")
		}
		return Value{Kind: Float, S: dto.PythonFloatStr(f)}, nil
	case domain.RequestObject, []any:
		return Value{Kind: JSON, J: v}, nil
	}
	return Value{}, unsupported("JSON value %T", raw)
}

// jsonValue is the Python object a JSONField holds for a decoded value.
func jsonValue(raw any) Value {
	if raw == nil {
		return NoneValue
	}
	return Value{Kind: JSON, J: raw}
}

var (
	isoDateTime = regexp.MustCompile(`^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$`)
	isoDate     = regexp.MustCompile(`^(\d{4})-(\d{2})-(\d{2})$`)
	isoTime     = regexp.MustCompile(`^(\d{2}):(\d{2})(?::(\d{2})(?:\.\d{1,6})?)?$`)
)

// ParseDateTime is django.utils.dateparse.parse_datetime for aware ISO
// timestamps (what the export writes); anything else is unsupported.
func ParseDateTime(text string) (time.Time, error) {
	m := isoDateTime.FindStringSubmatch(text)
	if m == nil {
		return time.Time{}, unsupported("datetime text")
	}
	atoi := func(s string) int { n, _ := strconv.Atoi(s); return n }
	year, month, day, hour, minute, second := atoi(m[1]), atoi(m[2]), atoi(m[3]), atoi(m[4]), atoi(m[5]), atoi(m[6])
	micro := 0
	if m[7] != "" {
		micro = atoi((m[7] + "000000")[:6])
	}
	offset := 0
	if m[8] != "Z" {
		sign := 1
		if m[8][0] == '-' {
			sign = -1
		}
		hours, minutes := atoi(m[8][1:3]), atoi(m[8][4:6])
		if hours > 23 || minutes > 59 {
			return time.Time{}, unsupported("datetime offset")
		}
		offset = sign * (hours*3600 + minutes*60)
	}
	// Years at the edges would overflow make_naive's conversion to UTC.
	if year < 2 || year > 9998 || month < 1 || month > 12 || hour > 23 || minute > 59 || second > 59 {
		return time.Time{}, unsupported("datetime range")
	}
	t := time.Date(year, time.Month(month), day, hour, minute, second, micro*1000, time.FixedZone("", offset))
	if t.Day() != day {
		return time.Time{}, unsupported("datetime day")
	}
	return t, nil
}

// ParseDate is parse_date for YYYY-MM-DD.
func ParseDate(text string) (string, error) {
	m := isoDate.FindStringSubmatch(text)
	if m == nil {
		return "", unsupported("date text")
	}
	year, _ := strconv.Atoi(m[1])
	month, _ := strconv.Atoi(m[2])
	day, _ := strconv.Atoi(m[3])
	if year < 1 || month < 1 || month > 12 {
		return "", unsupported("date range")
	}
	if time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC).Day() != day {
		return "", unsupported("date day")
	}
	return text, nil
}

// decimalText checks that text is str(Decimal(text)) and already has the
// column's decimal places within max_digits, so the in-memory str, the
// quantized database value and PostgreSQL's text agree.
func decimalText(field *Field, text string) error {
	pattern := `^-?(0|[1-9][0-9]*)`
	if field.DecimalPlaces > 0 {
		pattern += `\.[0-9]{` + strconv.Itoa(field.DecimalPlaces) + `}`
	}
	if !regexp.MustCompile(pattern + `$`).MatchString(text) {
		return unsupported("decimal text")
	}
	digits := strings.TrimLeft(strings.NewReplacer("-", "", ".", "").Replace(text), "0")
	if len(digits) > field.MaxDigits {
		return unsupported("decimal digits")
	}
	if strings.HasPrefix(text, "-") && digits == "" {
		return unsupported("negative zero")
	}
	return nil
}

// ParseArchived is AssociationImportService._parse_value(value, field) for a
// non-relation field, with the decoded JSON value Django would keep for the
// field types _parse_value passes through. Values whose in-memory form,
// stored form or audit text this package does not reproduce are unsupported.
func ParseArchived(field *Field, raw any) (Value, error) {
	if raw == nil {
		return NoneValue, nil
	}
	switch field.Kind {
	case "json":
		if number, ok := raw.(json.Number); ok {
			if _, err := FromJSON(number); err != nil {
				return Value{}, err
			}
		}
		return jsonValue(raw), nil
	case "uuid":
		text, ok := raw.(string)
		if !ok {
			return Value{}, unsupported("uuid value")
		}
		if text == "" {
			return NoneValue, nil
		}
		if !CanonicalUUID(text) {
			return Value{}, unsupported("uuid text")
		}
		return UUIDValue(text), nil
	case "datetime":
		text, ok := raw.(string)
		if !ok {
			return Value{}, unsupported("datetime value")
		}
		t, err := ParseDateTime(text)
		if err != nil {
			return Value{}, err
		}
		return DateTimeValue(t), nil
	case "date":
		text, ok := raw.(string)
		if !ok {
			return Value{}, unsupported("date value")
		}
		date, err := ParseDate(text)
		if err != nil {
			return Value{}, err
		}
		return Value{Kind: Date, S: date}, nil
	case "decimal":
		text, ok := raw.(string)
		if !ok {
			return Value{}, unsupported("decimal value")
		}
		if text == "" {
			return NoneValue, nil
		}
		if err := decimalText(field, text); err != nil {
			return Value{}, err
		}
		return Value{Kind: Decimal, S: text}, nil
	case "bool":
		b, ok := raw.(bool)
		if !ok {
			return Value{}, unsupported("bool value")
		}
		return BoolValue(b), nil
	case "int":
		value, err := FromJSON(raw)
		if err != nil {
			return Value{}, err
		}
		if value.Kind != Int {
			return Value{}, unsupported("int value")
		}
		return value, nil
	case "text":
		text, ok := raw.(string)
		if !ok {
			return Value{}, unsupported("text value")
		}
		return StrValue(text), nil
	case "time":
		text, ok := raw.(string)
		if !ok || !validTime(text) {
			return Value{}, unsupported("time value")
		}
		return StrValue(text), nil
	}
	return Value{}, unsupported("field kind %s", field.Kind)
}

func validTime(text string) bool {
	m := isoTime.FindStringSubmatch(text)
	if m == nil {
		return false
	}
	hour, _ := strconv.Atoi(m[1])
	minute, _ := strconv.Atoi(m[2])
	second := 0
	if m[3] != "" {
		second, _ = strconv.Atoi(m[3])
	}
	return hour < 24 && minute < 60 && second < 60
}

// ParseRelated is _parse_value(value, field.target_field) for a foreign key.
// value is a resolved key (canonical UUID text or an AutoField int) or the
// archived value.
func ParseRelated(field *Field, raw any) (Value, error) {
	if raw == nil {
		return NoneValue, nil
	}
	switch field.TargetKind {
	case "uuid":
		return ParseArchived(&Field{Kind: "uuid"}, raw)
	case "int":
		switch v := raw.(type) {
		case int64:
			return IntValue(v), nil
		case Value:
			if v.Kind == Int {
				return v, nil
			}
		}
		value, err := FromJSON(raw)
		if err != nil || value.Kind != Int {
			return Value{}, unsupported("int key")
		}
		return value, nil
	}
	return Value{}, unsupported("relation target %s", field.TargetKind)
}

// Default is field.get_default() for a field the record does not set.
func (e *Effects) Default(field *Field) (Value, error) {
	d := field.Default
	switch d.Kind {
	case "none":
		if field.Kind == "text" && !field.Null {
			return StrValue(""), nil
		}
		return NoneValue, nil
	case "value":
		switch d.Type {
		case "None":
			return NoneValue, nil
		case "bool":
			var b bool
			err := json.Unmarshal(d.Value, &b)
			return BoolValue(b), err
		case "int":
			var n json.Number
			if err := json.Unmarshal(d.Value, &n); err != nil {
				return Value{}, err
			}
			return FromJSON(n)
		case "str":
			var s string
			err := json.Unmarshal(d.Value, &s)
			return StrValue(s), err
		case "Decimal":
			var s string
			err := json.Unmarshal(d.Value, &s)
			return Value{Kind: Decimal, S: s}, err
		case "float":
			var s string
			err := json.Unmarshal(d.Value, &s)
			return Value{Kind: Float, S: s}, err
		}
	case "callable":
		switch d.Name {
		case "builtins.dict":
			return Value{Kind: JSON, J: domain.RequestObject{}}, nil
		case "builtins.list":
			return Value{Kind: JSON, J: []any{}}, nil
		case "django.utils.timezone.now":
			return DateTimeValue(e.now().UTC()), nil
		case "uuid.uuid4":
			return UUIDValue(e.newUUID()), nil
		}
	}
	return Value{}, unsupported("default %s %s %s", d.Kind, d.Type, d.Name)
}

// Text is str(value) (smart_str).
func (v Value) Text() (string, error) {
	switch v.Kind {
	case None:
		return "None", nil
	case Str, Int, Decimal, UUID, Date, Float:
		return v.S, nil
	case Bool:
		if v.B {
			return "True", nil
		}
		return "False", nil
	case DateTime:
		return pythonDateTimeStr(v.T), nil
	}
	return "", unsupported("str of %d", v.Kind)
}

// pythonDateTimeStr is str() of an aware datetime.
func pythonDateTimeStr(t time.Time) string {
	_, offset := t.Zone()
	sign := "+"
	if offset < 0 {
		sign, offset = "-", -offset
	}
	zone := sign + pad2(offset/3600) + ":" + pad2(offset%3600/60)
	if offset%60 != 0 {
		zone += ":" + pad2(offset%60)
	}
	return pythonNaive(t) + zone
}

// pythonNaive is str() of a naive datetime with t's wall time.
func pythonNaive(t time.Time) string {
	if t.Nanosecond()/1000 == 0 {
		return t.Format("2006-01-02 15:04:05")
	}
	return t.Format("2006-01-02 15:04:05.000000")
}

func pad2(n int) string {
	if n < 10 {
		return "0" + strconv.Itoa(n)
	}
	return strconv.Itoa(n)
}

// auditCreate is get_field_value(new, field) for the create diff: the
// rendered new value and whether it differs from the absent side's 'None'.
func auditCreate(field *Field, v Value) (string, bool, error) {
	switch {
	case field.IsFK():
		if v.IsNone() {
			return "None", true, nil
		}
		text, err := v.Text()
		return text, true, err
	case field.Type == "DateTimeField":
		if v.IsNone() {
			return "None", true, nil
		}
		if v.Kind != DateTime {
			return "", false, unsupported("datetime attribute")
		}
		return pythonNaive(v.T.UTC()), true, nil
	case field.Kind == "json":
		if field.Encoder != nil {
			return "", false, unsupported("json encoder")
		}
		text, err := jsonDumps(v, true)
		return text, true, err
	}
	text, err := v.Text()
	return text, err == nil && text != "None", err
}

// auditUpdateText is smart_str(get_field_value(obj, field)) for the text
// fields the import updates.
func auditUpdateText(field *Field, v Value) (string, error) {
	if field.Kind != "text" || (v.Kind != Str && v.Kind != None) {
		return "", unsupported("update audit of %s", field.Name)
	}
	return v.Text()
}

func jsonDumps(v Value, sorted bool) (string, error) {
	var value any
	switch v.Kind {
	case None:
		value = nil
	case JSON:
		value = v.J
	default:
		return "", unsupported("json attribute")
	}
	if err := checkJSONNumbers(value); err != nil {
		return "", err
	}
	if sorted {
		return string(dto.PythonJSONDumpsSorted(value)), nil
	}
	return string(dto.PythonJSONDumps(value)), nil
}

func checkJSONNumbers(value any) error {
	switch v := value.(type) {
	case json.Number:
		_, err := FromJSON(v)
		return err
	case domain.RequestObject:
		for _, item := range v {
			if err := checkJSONNumbers(item.Value); err != nil {
				return err
			}
		}
	case []any:
		for _, item := range v {
			if err := checkJSONNumbers(item); err != nil {
				return err
			}
		}
	}
	return nil
}

// dbValue is field.get_db_prep_save(value): the text bound for the column
// (cast to its database type) or NULL.
func dbValue(field *Field, v Value) (any, error) {
	if v.IsNone() {
		if field.Kind == "json" {
			return nil, nil
		}
		return nil, nil
	}
	kind := field.Kind
	if field.IsFK() {
		kind = field.TargetKind
	}
	switch kind {
	case "text":
		if v.Kind == Str {
			return v.S, nil
		}
	case "uuid":
		if v.Kind == UUID {
			return v.S, nil
		}
	case "int":
		if v.Kind == Int {
			return v.S, nil
		}
	case "bool":
		if v.Kind == Bool {
			return strconv.FormatBool(v.B), nil
		}
	case "decimal":
		switch v.Kind {
		case Decimal:
			return quantize(field, v.S)
		case Int:
			return quantize(field, v.S)
		case Float:
			if v.S == "0.0" {
				return quantize(field, "0")
			}
		}
	case "datetime":
		if v.Kind == DateTime {
			return v.T.Format(time.RFC3339Nano), nil
		}
	case "date":
		switch v.Kind {
		case Date:
			return v.S, nil
		case DateTime:
			return v.T.UTC().Format("2006-01-02"), nil
		}
	case "time":
		if v.Kind == Str && validTime(v.S) {
			return v.S, nil
		}
	case "json":
		text, err := jsonDumps(v, false)
		if err != nil {
			return nil, err
		}
		return text, nil
	}
	return nil, unsupported("value kind %d for %s", v.Kind, field.Name)
}

// quantize is format_number(value, max_digits, decimal_places) for values
// that need no rounding.
func quantize(field *Field, text string) (any, error) {
	whole, fraction, _ := strings.Cut(text, ".")
	if len(fraction) > field.DecimalPlaces {
		return nil, unsupported("decimal rounding")
	}
	if field.DecimalPlaces > 0 {
		text = whole + "." + fraction + strings.Repeat("0", field.DecimalPlaces-len(fraction))
	}
	digits := strings.TrimLeft(strings.NewReplacer("-", "", ".", "").Replace(text), "0")
	if len(digits) > field.MaxDigits {
		return nil, unsupported("decimal digits")
	}
	return text, nil
}
