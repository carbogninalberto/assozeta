package djtemplate

import (
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// Context values. The renderer accepts exactly these types; anything else
// is refused with ErrUnsupported.
//
//	nil        Python None
//	string     str
//	Safe       SafeString (mark_safe)
//	bool       bool
//	int64      int
//	*big.Int   int beyond int64 (a JSON column's integer)
//	float64    float (including ±Inf, as Python's float() reads an overflowing
//	           decimal)
//	Decimal    decimal.Decimal, given by its str()
//	Date       datetime.date
//	DateTime   aware datetime.datetime (rendered in UTC, Django's TIME_ZONE)
//	*Dict      dict with str keys, in insertion order
//	List       list
//	Tuple      tuple (dict.items() entries)
//	*Object    a model instance (or another object read by attribute)
//	Lazy       an attribute Django computes when read (a method or property)

// Safe is a string marked safe.
type Safe string

// Decimal is a decimal.Decimal given by its str() (for example "10.50").
type Decimal string

// Date is datetime.date.
type Date struct{ Year, Month, Day int }

// DateTime is an aware datetime.datetime; it is rendered in UTC.
type DateTime struct{ Time time.Time }

// List is a Python list.
type List []any

// Tuple is a Python tuple.
type Tuple []any

// Lazy is an attribute evaluated when the template reads it: a property or
// a method Django calls. Its error stops the render.
type Lazy func() (any, error)

// Dict is a Python dict with str keys in insertion order.
type Dict struct {
	keys   []string
	values map[string]any
}

// NewDict returns an empty dict.
func NewDict() *Dict { return &Dict{values: map[string]any{}} }

// Set inserts or replaces a key (a replaced key keeps its position).
func (d *Dict) Set(key string, value any) *Dict {
	if _, ok := d.values[key]; !ok {
		d.keys = append(d.keys, key)
	}
	d.values[key] = value
	return d
}

// Get returns a key's value.
func (d *Dict) Get(key string) (any, bool) {
	value, ok := d.values[key]
	return value, ok
}

// Keys returns the keys in order.
func (d *Dict) Keys() []string { return append([]string(nil), d.keys...) }

// Len is len(dict).
func (d *Dict) Len() int { return len(d.keys) }

// Object is a model instance: the attributes a template may read. Reading
// an attribute it does not declare is refused (the port must declare every
// attribute the templates read); Missing declares one Django lacks.
type Object struct {
	Class string
	attrs map[string]any
	open  bool // tests: an undeclared attribute is missing
}

// Missing declares an attribute the Python object does not have.
var Missing = missing{}

type missing struct{}

// NewObject returns a model instance with the given attributes.
func NewObject(class string, attrs map[string]any) *Object {
	copied := make(map[string]any, len(attrs))
	for key, value := range attrs {
		copied[key] = value
	}
	return &Object{Class: class, attrs: copied}
}

// Set replaces an attribute (setattr).
func (o *Object) Set(name string, value any) { o.attrs[name] = value }

// Attr returns a declared attribute.
func (o *Object) Attr(name string) (any, bool) {
	value, ok := o.attrs[name]
	return value, ok
}

// Today and the current date are the caller's: Date helpers.

// DateOf is the date of a civil time.
func DateOf(t time.Time) Date { return Date{Year: t.Year(), Month: int(t.Month()), Day: t.Day()} }

func (d Date) time() time.Time {
	return time.Date(d.Year, time.Month(d.Month), d.Day, 0, 0, 0, 0, time.UTC)
}

// String is str(date): YYYY-MM-DD.
func (d Date) String() string { return fmt.Sprintf("%04d-%02d-%02d", d.Year, d.Month, d.Day) }

// pythonFloatRepr is repr(float) (and so str(float)): Python's shortest
// round-trip text, with inf, -inf and nan for the non-finite values
// dto.PythonFloatStr writes as JSON spells them.
func pythonFloatRepr(value float64) (string, error) {
	switch text := dto.PythonFloatStr(value); text {
	case "Infinity":
		return "inf", nil
	case "-Infinity":
		return "-inf", nil
	case "NaN":
		return "nan", nil
	default:
		return text, nil
	}
}

// pythonRepr is repr(value) for the context's types: dto.PythonRepr for the
// scalars (its quoting and float notation are str.__repr__'s) and a recursive
// repr for the containers a JSON column can nest.
func pythonRepr(value any) (string, error) {
	switch v := value.(type) {
	case nil, bool, string, float64:
		return dto.PythonRepr(v), nil
	case Safe:
		return dto.PythonRepr(string(v)), nil
	case int64:
		return strconv.FormatInt(v, 10), nil
	case *big.Int:
		return v.String(), nil
	case Decimal:
		if _, err := decimalRat(v); err != nil {
			return "", err
		}
		return "Decimal('" + string(v) + "')", nil
	case List:
		items := make([]string, len(v))
		for i, item := range v {
			text, err := pythonRepr(item)
			if err != nil {
				return "", err
			}
			items[i] = text
		}
		return "[" + strings.Join(items, ", ") + "]", nil
	case Tuple:
		items := make([]string, len(v))
		for i, item := range v {
			text, err := pythonRepr(item)
			if err != nil {
				return "", err
			}
			items[i] = text
		}
		joined := strings.Join(items, ", ")
		if len(items) == 1 {
			return "(" + joined + ",)", nil
		}
		return "(" + joined + ")", nil
	case *Dict:
		items := make([]string, 0, v.Len())
		for _, key := range v.Keys() {
			item, _ := v.Get(key)
			text, err := pythonRepr(item)
			if err != nil {
				return "", err
			}
			items = append(items, dto.PythonRepr(key)+": "+text)
		}
		return "{" + strings.Join(items, ", ") + "}", nil
	}
	return "", unsupported("repr of %T", value)
}

// PythonStr is str(value): strings unchanged, ints in decimal, floats by
// repr (inf, -inf, nan) and lists, tuples and dicts by their Python repr.
func PythonStr(value any) (string, error) {
	switch v := value.(type) {
	case nil:
		return "None", nil
	case string:
		return v, nil
	case Safe:
		return string(v), nil
	case bool:
		if v {
			return "True", nil
		}
		return "False", nil
	case int64:
		return strconv.FormatInt(v, 10), nil
	case *big.Int:
		return v.String(), nil
	case float64:
		return pythonFloatRepr(v)
	case Decimal:
		if _, err := decimalRat(v); err != nil {
			return "", err
		}
		return string(v), nil
	case Date:
		return v.String(), nil
	case DateTime:
		return pythonDateTimeStr(v.Time), nil
	case List, Tuple, *Dict:
		return pythonRepr(value)
	}
	return "", unsupported("str() of %T", value)
}

// pythonDateTimeStr is str() of an aware datetime in UTC.
func pythonDateTimeStr(t time.Time) string {
	t = t.UTC()
	text := t.Format("2006-01-02 15:04:05")
	if micro := t.Nanosecond() / 1000; micro != 0 {
		text += fmt.Sprintf(".%06d", micro)
	}
	return text + "+00:00"
}

// decimalRat parses the finite str() forms of a Decimal.
func decimalRat(d Decimal) (*big.Rat, error) {
	text := string(d)
	if text == "" || len(text) > 64 || strings.ContainsAny(text, " _/") || strings.ContainsAny(strings.ToLower(text), "inaxp") {
		return nil, unsupported("decimal %q", text)
	}
	value, ok := new(big.Rat).SetString(text)
	if !ok {
		return nil, unsupported("decimal %q", text)
	}
	return value, nil
}

// numeric is the exact value of a Python number (bool, int, float, Decimal).
func numeric(value any) (*big.Rat, bool, error) {
	switch v := value.(type) {
	case bool:
		if v {
			return big.NewRat(1, 1), true, nil
		}
		return new(big.Rat), true, nil
	case int64:
		return new(big.Rat).SetInt64(v), true, nil
	case *big.Int:
		return new(big.Rat).SetInt(v), true, nil
	case float64:
		if math.IsInf(v, 0) || math.IsNaN(v) {
			return nil, true, unsupported("non-finite float")
		}
		return new(big.Rat).SetFloat64(v), true, nil
	case Decimal:
		r, err := decimalRat(v)
		return r, true, err
	}
	return nil, false, nil
}

// pythonUpper is str.upper(): the full Unicode case mapping (SpecialCasing
// included: 'ß' is "SS").
func pythonUpper(text string) (string, error) { return dto.PythonUpper(text), nil }

// pythonLower is str.lower(): the full Unicode case mapping (SpecialCasing
// included: a final Greek sigma and 'İ').
func pythonLower(text string) (string, error) { return dto.PythonLower(text), nil }

// Truthy is bool(value).
func Truthy(value any) (bool, error) {
	switch v := value.(type) {
	case nil, missing:
		return false, nil
	case bool:
		return v, nil
	case string:
		return v != "", nil
	case Safe:
		return v != "", nil
	case int64:
		return v != 0, nil
	case *big.Int:
		return v.Sign() != 0, nil
	case float64:
		return v != 0, nil
	case Decimal:
		r, err := decimalRat(v)
		return err == nil && r.Sign() != 0, err
	case Date, DateTime, *Object:
		return true, nil
	case *Dict:
		return v.Len() > 0, nil
	case List:
		return len(v) > 0, nil
	case Tuple:
		return len(v) > 0, nil
	}
	return false, unsupported("truth value of %T", value)
}
