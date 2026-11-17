package importeffects

import (
	"encoding/json"
	"errors"
	"fmt"
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
	Float         // float (S holds repr(value))
	Decimal       // decimal.Decimal (S holds str(value))
	UUID          // uuid.UUID (S holds the canonical text)
	DateTime      // datetime.datetime (T, Naive, OffMicro)
	Date          // datetime.date (S holds isoformat())
	JSON          // a decoded JSON container (J: domain.RequestObject or []any)
)

// Value is one in-memory model attribute: what Django's model field holds
// after Model.__init__ (and the importer's _parse_value), exactly as CPython
// would hold the decoded JSON value.
type Value struct {
	Kind Kind
	S    string
	B    bool
	T    time.Time
	J    any
	// Naive reports a datetime.datetime without tzinfo; OffMicro is the
	// sub-second part of a fixed offset (CPython accepts fractional offsets).
	Naive    bool
	OffMicro int
}

// NoneValue is Python's None.
var NoneValue = Value{}

// StrValue is a str.
func StrValue(text string) Value { return Value{Kind: Str, S: text} }

// UUIDValue is a uuid.UUID from its canonical text.
func UUIDValue(text string) Value { return Value{Kind: UUID, S: text} }

// IntValue is an int.
func IntValue(n int64) Value { return Value{Kind: Int, S: strconv.FormatInt(n, 10)} }

// DateTimeValue is an aware datetime in t's location.
func DateTimeValue(t time.Time) Value { return Value{Kind: DateTime, T: t} }

// BoolValue is a bool.
func BoolValue(b bool) Value { return Value{Kind: Bool, B: b} }

// IsNone reports Python None.
func (v Value) IsNone() bool { return v.Kind == None }

// moment is the pyMoment a DateTime value holds.
func (v Value) moment() pyMoment { return pyMoment{t: v.T, naive: v.Naive, offMicro: v.OffMicro} }

var canonicalUUID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

// CanonicalUUID reports text equal to str(uuid.UUID(text)).
func CanonicalUUID(text string) bool { return canonicalUUID.MatchString(text) }

// FromJSON is a decoded JSON value as Python's json module returns it
// (dto.ParseProfileTablesJSON's representation: json.Number for numbers,
// domain.RequestObject for objects, float64 for the NaN/Infinity literals).
func FromJSON(raw any) (Value, error) { return rawValue(raw) }

// rawValue converts a decoded JSON value to its Python value.
func rawValue(raw any) (Value, error) {
	switch v := raw.(type) {
	case nil:
		return NoneValue, nil
	case string:
		return StrValue(v), nil
	case bool:
		return BoolValue(v), nil
	case int64:
		return Value{Kind: Int, S: strconv.FormatInt(v, 10)}, nil
	case int:
		return Value{Kind: Int, S: strconv.Itoa(v)}, nil
	case json.Number:
		token := string(v)
		switch token {
		case "NaN", "nan":
			return Value{Kind: Float, S: "nan"}, nil
		case "Infinity", "inf":
			return Value{Kind: Float, S: "inf"}, nil
		case "-Infinity", "-inf":
			return Value{Kind: Float, S: "-inf"}, nil
		}
		if !strings.ContainsAny(token, ".eE") {
			digits, ok := new(big.Int).SetString(token, 10)
			if !ok {
				return Value{}, planUnreachable("JSON number token %q", token)
			}
			return Value{Kind: Int, S: digits.String()}, nil
		}
		f, err := strconv.ParseFloat(token, 64)
		if err != nil && !errors.Is(err, strconv.ErrRange) {
			// json.loads parses every well formed number: an overflow is
			// +/-inf, an underflow a signed zero.
			return Value{}, planUnreachable("JSON number token %q", token)
		}
		return Value{Kind: Float, S: pyFloatText(f)}, nil
	case float64:
		return Value{Kind: Float, S: pyFloatText(v)}, nil
	case domain.RequestObject, []any:
		return Value{Kind: JSON, J: v}, nil
	case Value:
		return v, nil
	}
	return Value{}, planUnreachable("decoded JSON value %T", raw)
}

// ParseArchived is AssociationImportService._parse_value(value, field) for a
// non-relation field: the field's own Python type decides, everything else is
// kept as the decoded JSON value.
func ParseArchived(field *Field, raw any) (Value, error) {
	return parseForType(field.Type, field, raw)
}

// ParseRelated is _parse_value(value, field.target_field) for a foreign key.
// value is a resolved key (canonical UUID text or an integer) or the archived
// value.
func ParseRelated(field *Field, raw any) (Value, error) {
	return parseForType(field.TargetType, nil, raw)
}

func parseForType(fieldType string, field *Field, raw any) (Value, error) {
	value, err := rawValue(raw)
	if err != nil {
		return Value{}, err
	}
	if value.IsNone() {
		return NoneValue, nil
	}
	switch fieldType {
	case "UUIDField":
		return parseUUIDValue(value)
	case "DateTimeField":
		if value.Kind != Str {
			return value, nil
		}
		moment, err := parseDateTime(value.S)
		if err != nil {
			return Value{}, err
		}
		if moment == nil {
			return NoneValue, nil
		}
		return DateTimeValue(moment.t).withMoment(*moment), nil
	case "DateField":
		if value.Kind != Str {
			return value, nil
		}
		date, err := parseDate(value.S)
		if err != nil {
			return Value{}, err
		}
		if date == nil {
			return NoneValue, nil
		}
		return Value{Kind: Date, S: date.String()}, nil
	case "DecimalField":
		if !pyTruthy(value) {
			return NoneValue, nil
		}
		decimal, err := decimalFromValue(value)
		if err != nil {
			return Value{}, err
		}
		return Value{Kind: Decimal, S: decimal.String()}, nil
	case "BooleanField":
		return BoolValue(pyTruthy(value)), nil
	case "IntegerField", "PositiveIntegerField", "PositiveSmallIntegerField":
		digits, err := pyIntFromValue(value)
		if err != nil {
			return Value{}, err
		}
		return Value{Kind: Int, S: digits}, nil
	}
	// CharField, TextField, EmailField, AutoField/BigAutoField, TimeField and
	// JSONField keep the decoded value (plan.json: the imported models' field
	// types; _parse_value converts only the types above).
	return value, nil
}

// withMoment is a DateTime value with the parsed moment's zone information.
func (v Value) withMoment(m pyMoment) Value {
	v.Kind, v.T, v.Naive, v.OffMicro = DateTime, m.t, m.naive, m.offMicro
	return v
}

// parseUUIDValue is uuid.UUID(value) for a non-empty, non-UUID value.
func parseUUIDValue(v Value) (Value, error) {
	if v.Kind == UUID {
		return v, nil
	}
	if v.Kind != Str {
		return Value{}, djangoError(fmt.Sprintf("'%s' object has no attribute 'replace'", pyTypeName(v)))
	}
	if v.S == "" {
		return NoneValue, nil
	}
	text := strings.ReplaceAll(v.S, "urn:", "")
	text = strings.ReplaceAll(text, "uuid:", "")
	text = strings.Trim(text, "{}")
	text = strings.ReplaceAll(text, "-", "")
	if len(text) != 32 {
		return Value{}, djangoError("badly formed hexadecimal UUID string")
	}
	number, ok := new(big.Int).SetString(text, 16)
	if !ok {
		return Value{}, djangoError("invalid literal for int() with base 16: " + dto.PythonStringRepr(text))
	}
	return UUIDValue(formatUUID(number)), nil
}

// formatUUID is str(uuid.UUID(int=number)).
func formatUUID(number *big.Int) string {
	hex := fmt.Sprintf("%032x", number)
	return hex[:8] + "-" + hex[8:12] + "-" + hex[12:16] + "-" + hex[16:20] + "-" + hex[20:]
}

// uuidText is UUIDField.to_python + get_db_prep_value for an in-memory value.
func uuidText(v Value) (string, error) {
	if v.Kind == UUID {
		return v.S, nil
	}
	if v.Kind == Str {
		parsed, err := parseUUIDValue(v)
		if err != nil {
			return "", validationError(fmt.Sprintf("\"%s\" non è uno UUID valido.", v.S))
		}
		if parsed.IsNone() {
			return "", validationError(`"" non è uno UUID valido.`)
		}
		return parsed.S, nil
	}
	switch v.Kind {
	case Int, Bool:
		digits, err := pyIntFromValue(v)
		if err != nil {
			return "", err
		}
		number, _ := new(big.Int).SetString(digits, 10)
		if number.Sign() < 0 || number.Cmp(new(big.Int).Lsh(big.NewInt(1), 128)) >= 0 {
			return "", djangoError("int is out of range (need a 128-bit value)")
		}
		return formatUUID(number), nil
	}
	return "", validationError(fmt.Sprintf("\"%s\" non è uno UUID valido.", mustText(v)))
}

// ParseDateTime is django.utils.dateparse.parse_datetime for its callers: nil
// when the text is not well formatted (Django's None), a *DjangoError for a
// well formed but invalid value.
func ParseDateTime(text string) (*Value, error) {
	moment, err := parseDateTime(text)
	if err != nil {
		return nil, err
	}
	if moment == nil {
		return nil, nil
	}
	value := DateTimeValue(moment.t).withMoment(*moment)
	return &value, nil
}

// ParseDate is django.utils.dateparse.parse_date; nil for text that is not a
// date.
func ParseDate(text string) (*Value, error) {
	date, err := parseDate(text)
	if err != nil {
		return nil, err
	}
	if date == nil {
		return nil, nil
	}
	value := Value{Kind: Date, S: date.String()}
	return &value, nil
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
			f, err := strconv.ParseFloat(s, 64)
			if err != nil && !math.IsInf(f, 0) {
				return Value{}, err
			}
			return Value{Kind: Float, S: pyFloatText(f)}, nil
		}
		return Value{}, planUnreachable("default %s %s", d.Kind, d.Type)
	case "callable":
		return e.callableDefault(field, d.Name)
	}
	return Value{}, planUnreachable("default %s", d.Kind)
}

// Text is str(value) (smart_str).
func (v Value) Text() (string, error) {
	switch v.Kind {
	case None:
		return "None", nil
	case Str, Decimal, UUID, Date, Float:
		return v.S, nil
	case Int:
		return pyIntText(v.S)
	case Bool:
		if v.B {
			return "True", nil
		}
		return "False", nil
	case DateTime:
		return v.moment().String(), nil
	case JSON:
		return jsonRepr(v.J)
	}
	return "", planUnreachable("str() of kind %d", v.Kind)
}

// Repr is repr(value).
func (v Value) Repr() (string, error) {
	switch v.Kind {
	case None:
		return "None", nil
	case Str:
		return dto.PythonStringRepr(v.S), nil
	case Int:
		return pyIntText(v.S)
	case Bool:
		if v.B {
			return "True", nil
		}
		return "False", nil
	case Float:
		return v.S, nil
	case Date:
		return pythonDateRepr(v.S), nil
	case Decimal:
		return "Decimal('" + v.S + "')", nil
	case UUID:
		return "UUID('" + v.S + "')", nil
	case DateTime:
		return pythonDatetimeRepr(v.moment()), nil
	case JSON:
		return jsonRepr(v.J)
	}
	return "", planUnreachable("repr() of kind %d", v.Kind)
}

// jsonRepr is str() of a decoded JSON container (its Python repr).
// dto.PythonStr is the existing helper for repr of a decoded JSON value; the
// corpus in pyrepr_corpus_test.go checks it against CPython for quotes, control
// characters, printable/non-printable Unicode, astral characters, inf/nan,
// -0.0 and huge ints, which is why it is reused here. djtemplate.PythonStr
// renders djtemplate's own Value types, not a decoded JSON tree.
func jsonRepr(value any) (string, error) {
	if err := checkJSONInts(value); err != nil {
		return "", err
	}
	return dto.PythonStr(value), nil
}

// checkJSONInts applies CPython's 4300-digit limit to the ints json.dumps and
// str() would convert.
func checkJSONInts(value any) error {
	switch v := value.(type) {
	case json.Number:
		token := string(v)
		if strings.ContainsAny(token, ".eE") || token == "NaN" || token == "Infinity" || token == "-Infinity" {
			return nil
		}
		if len(strings.TrimPrefix(token, "-")) > maxIntStringDigits {
			return djangoError("Exceeds the limit (4300 digits) for integer string conversion; use sys.set_int_max_str_digits() to increase the limit")
		}
	case domain.RequestObject:
		for _, field := range v {
			if err := checkJSONInts(field.Value); err != nil {
				return err
			}
		}
	case []any:
		for _, item := range v {
			if err := checkJSONInts(item); err != nil {
				return err
			}
		}
	}
	return nil
}

// auditCreate is get_field_value(new, field) for the create diff: the rendered
// new value and whether it differs from the absent side's "None".
func auditCreate(field *Field, v Value) (string, bool, error) {
	text, err := auditFieldText(field, v)
	if err != nil {
		return "", false, err
	}
	switch {
	case field.IsFK(), field.Type == "DateTimeField", field.Kind == "json":
		// get_field_value leaves None/NoneType (FK, datetime) or json.dumps'
		// text (JSON) here, and the absent side is the "None" string, so the
		// comparison always differs and the pair is stored.
		return text, true, nil
	}
	return text, text != "None", nil
}

// auditUpdateText is smart_str(get_field_value(obj, field)).
func auditUpdateText(field *Field, v Value) (string, error) {
	return auditFieldText(field, v)
}

// auditFieldText is auditlog's get_field_value(obj, field) plus smart_str, for
// an in-memory attribute.
func auditFieldText(field *Field, v Value) (string, error) {
	switch {
	case field.IsFK():
		// smart_str(getattr(obj, field.get_attname()), strings_only=True):
		// integers stay integers and the diff's final smart_str renders them.
		if v.IsNone() {
			return "None", nil
		}
		if v.Kind == Int {
			return pyIntText(v.S)
		}
		return v.Text()
	case field.Type == "DateTimeField":
		moment, err := dateTimeToPython(v)
		if err != nil {
			if isTypeError(err) {
				// get_field_value's `except TypeError: return value`.
				return v.Text()
			}
			return "", err
		}
		if moment == nil {
			return "None", nil
		}
		return pythonNaive(moment.utc()), nil
	case field.Kind == "json":
		if field.Encoder != nil {
			return "", planUnreachable("json encoder %s on %s", *field.Encoder, field.Name)
		}
		return jsonDumpsValue(v, true)
	}
	return v.Text()
}

func isTypeError(err error) bool {
	django, ok := err.(*DjangoError)
	return ok && django.Class == "TypeError"
}

// dateTimeToPython is DateTimeField.to_python.
func dateTimeToPython(v Value) (*pyMoment, error) {
	switch v.Kind {
	case None:
		return nil, nil
	case DateTime:
		moment := v.moment()
		return &moment, nil
	case Date:
		date, err := parsePyDate(v.S)
		if err != nil {
			return nil, err
		}
		moment, err := newPyMoment(date.year, date.month, date.day, 0, 0, 0, 0, time.UTC, true)
		if err != nil {
			return nil, err
		}
		// A date value is warned about and made aware in the default zone.
		moment.naive = false
		return &moment, nil
	case Str:
		moment, err := parseDateTime(v.S)
		if err != nil || moment != nil {
			return moment, err
		}
		date, err := parseDate(v.S)
		if err != nil {
			if isValueError(err) {
				return nil, dateTimeValidation("invalid_date", v.S)
			}
			return nil, err
		}
		if date != nil {
			built, err := newPyMoment(date.year, date.month, date.day, 0, 0, 0, 0, time.UTC, true)
			if err != nil {
				return nil, err
			}
			return &built, nil
		}
		return nil, dateTimeValidation("invalid", v.S)
	}
	return nil, typeError("fromisoformat: argument must be str")
}

func isValueError(err error) bool {
	django, ok := err.(*DjangoError)
	return !ok || django.Class == "" || django.Class == "ValueError"
}

// dateTimeValidation is DateTimeField's ValidationError messages.
func dateTimeValidation(kind, value string) error {
	switch kind {
	case "invalid_datetime":
		return validationError(fmt.Sprintf("Il valore di \"%s\" ha il formato corretto (AAAA-MM-GG HH:MM[:ss[.uuuuuu]][TZ]) ma non è una data/ora valida.", value))
	case "invalid_date":
		return validationError(fmt.Sprintf("Il valore di \"%s\" ha il formato corretto (AAAA-MM-GG) ma non è una data valida.", value))
	}
	return validationError(fmt.Sprintf("Il valore \"%s\" ha un formato non valido. Deve essere nel formato AAAA-MM-GG HH:MM[:ss[.uuuuuu]][TZ].", value))
}

// parsePyDate is date.fromisoformat for a YYYY-MM-DD text.
func parsePyDate(text string) (pyDate, error) {
	date, err := parseDate(text)
	if err != nil {
		return pyDate{}, err
	}
	if date == nil {
		return pyDate{}, djangoError("Invalid isoformat string: " + dto.PythonStringRepr(text))
	}
	return *date, nil
}

// jsonDumpsValue is json.dumps(value, sort_keys=sorted).
func jsonDumpsValue(v Value, sorted bool) (string, error) {
	value, err := pythonJSONValue(v)
	if err != nil {
		return "", err
	}
	if err := checkJSONInts(value); err != nil {
		return "", err
	}
	if sorted {
		return string(dto.PythonJSONDumpsSorted(value)), nil
	}
	return string(dto.PythonJSONDumps(value)), nil
}

// pythonJSONValue is the value json.dumps receives.
func pythonJSONValue(v Value) (any, error) {
	switch v.Kind {
	case None:
		return nil, nil
	case JSON:
		return v.J, nil
	case Str:
		return v.S, nil
	case Int:
		if _, err := pyIntText(v.S); err != nil {
			return nil, err
		}
		return json.Number(v.S), nil
	case Bool:
		return v.B, nil
	case Float:
		return pyFloatValue(v.S)
	}
	return nil, planUnreachable("json.dumps of kind %d", v.Kind)
}

// dbValue is field.get_db_prep_save(value): the text bound for the column
// (cast to its database type) or NULL.
func dbValue(field *Field, v Value) (any, error) {
	if v.IsNone() {
		return nil, nil
	}
	kind := field.Kind
	if field.IsFK() {
		kind = field.TargetKind
	}
	switch kind {
	case "text":
		return v.Text()
	case "uuid":
		return uuidText(v)
	case "int":
		return intFieldPrep(field.Name, v)
	case "bool":
		b, err := boolFieldPrep(field, v)
		if err != nil {
			return nil, err
		}
		return b, nil
	case "decimal":
		return decimalFieldPrep(v)
	case "datetime":
		moment, err := dateTimePrep(v)
		if err != nil {
			return nil, err
		}
		if moment == nil {
			return nil, nil
		}
		return moment.String(), nil
	case "date":
		return datePrep(v)
	case "time":
		return timePrep(v)
	case "json":
		return jsonDumpsValue(v, false)
	}
	return nil, planUnreachable("field kind %s of %s", kind, field.Name)
}

// intFieldPrep is IntegerField.get_prep_value: int(value), with its
// "expected a number" wrapper for the errors int() raises.
func intFieldPrep(name string, v Value) (string, error) {
	digits, err := pyIntFromValue(v)
	if err != nil {
		if django, ok := err.(*DjangoError); ok && (django.Class == "" || django.Class == "TypeError" || django.Class == "ValueError") {
			repr, reprErr := v.Repr()
			if reprErr != nil {
				repr = mustText(v)
			}
			return "", djangoError(fmt.Sprintf("Field '%s' expected a number but got %s.", name, repr))
		}
		return "", err
	}
	return pyIntText(digits)
}

// boolFieldPrep is BooleanField.to_python for a non-None value.
func boolFieldPrep(field *Field, v Value) (string, error) {
	switch v.Kind {
	case Bool:
		return strconv.FormatBool(v.B), nil
	case Int:
		return strconv.FormatBool(strings.TrimLeft(v.S, "0-") != ""), nil
	case Str:
		switch v.S {
		case "t", "True", "1":
			return "true", nil
		case "f", "False", "0":
			return "false", nil
		}
	}
	return "", boolValidation(field.Null, mustText(v))
}

func boolValidation(nullable bool, value string) error {
	if nullable {
		return validationError(fmt.Sprintf("Il valore \"%s\" deve essere True, False oppure None.", value))
	}
	return validationError(fmt.Sprintf("Il valore \"%s\" deve essere True oppure False.", value))
}

// decimalFieldPrep is DecimalField.to_python + get_db_prep_save. psycopg2
// binds str(Decimal), so the column's precision and scale are PostgreSQL's.
func decimalFieldPrep(v Value) (any, error) {
	decimal, err := decimalFromValue(v)
	if err != nil {
		return nil, err
	}
	if decimal.special != "" {
		return nil, errDecimalNotANumber(v)
	}
	return decimal.String(), nil
}

// dateTimePrep is DateTimeField.get_prep_value + adapt_datetimefield_value:
// naive values are made aware in the default timezone (UTC) and the bound text
// is str(value).
func dateTimePrep(v Value) (*pyMoment, error) {
	switch v.Kind {
	case DateTime:
		moment := v.moment()
		if moment.naive {
			moment.naive = false
		}
		return &moment, nil
	case Date:
		date, err := parsePyDate(v.S)
		if err != nil {
			return nil, err
		}
		moment, err := newPyMoment(date.year, date.month, date.day, 0, 0, 0, 0, time.UTC, false)
		if err != nil {
			return nil, err
		}
		return &moment, nil
	case Str:
		moment, err := dateTimeToPython(v)
		if err != nil {
			return nil, err
		}
		if moment == nil {
			return nil, nil
		}
		if moment.naive {
			moment.naive = false
		}
		return moment, nil
	}
	return nil, typeError("fromisoformat: argument must be str")
}

// datePrep is DateField.get_prep_value + adapt_datefield_value.
func datePrep(v Value) (any, error) {
	switch v.Kind {
	case Date:
		return v.S, nil
	case DateTime:
		moment := v.moment()
		if !moment.naive {
			return moment.utc().Format("2006-01-02"), nil
		}
		return moment.t.Format("2006-01-02"), nil
	case Str:
		date, err := parseDate(v.S)
		if err != nil {
			if isValueError(err) {
				return nil, validationError(fmt.Sprintf("Il valore di \"%s\" ha il formato corretto (AAAA-MM-GG) ma non è una data valida.", v.S))
			}
			return nil, err
		}
		if date == nil {
			return nil, validationError(fmt.Sprintf("Il valore \"%s\" ha un formato di data non valido. Deve essere nel formato AAAA-MM-GG.", v.S))
		}
		return date.String(), nil
	}
	// DateField.to_python calls date.fromisoformat for anything else: a
	// non-str raises CPython's TypeError.
	return nil, typeError("fromisoformat: argument must be str")
}

// timePrep is TimeField.get_prep_value + adapt_timefield_value.
func timePrep(v Value) (any, error) {
	if v.Kind != Str {
		return nil, typeError("fromisoformat: argument must be str")
	}
	parsed, err := parseTime(v.S)
	if err != nil {
		if isValueError(err) {
			return nil, validationError(fmt.Sprintf("Il valore di \"%s\" ha il corretto formato (HH:MM[:ss[.uuuuuu]]) ma non è un orario valido.", v.S))
		}
		return nil, err
	}
	if parsed == nil {
		return nil, validationError(fmt.Sprintf("Il valore di \"%s\" ha un formato non valido. Deve essere nel formato HH:MM[:ss[.uuuuuu]].", v.S))
	}
	return parsed.String(), nil
}
