package djtemplate

import (
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/emailrender"
)

// Context values. The renderer accepts exactly these types; anything else
// is refused with ErrUnsupported.
//
//	nil        Python None
//	string     str
//	Safe       SafeString (mark_safe)
//	bool       bool
//	int64      int
//	float64    float (finite)
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

// PythonStr is str(value) for the scalar types (str() of containers and
// objects is refused).
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
	case float64:
		return emailrender.FloatRepr(v)
	case Decimal:
		if _, err := decimalRat(v); err != nil {
			return "", err
		}
		return string(v), nil
	case Date:
		return v.String(), nil
	case DateTime:
		return pythonDateTimeStr(v.Time), nil
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

// Python's str.upper()/str.lower() use full case mappings (and the final
// sigma rule); Go's simple mappings agree except for these runes, which
// are refused.
func specialUpper(r rune) bool {
	switch {
	case r == 0xDF, r == 0x149, r == 0x1F0, r == 0x390, r == 0x3B0, r == 0x587:
		return true
	case r >= 0x1E96 && r <= 0x1E9A, r >= 0x1F50 && r <= 0x1F56, r >= 0x1F80 && r <= 0x1FFC,
		r >= 0xFB00 && r <= 0xFB06, r >= 0xFB13 && r <= 0xFB17:
		return true
	}
	return false
}

func specialLower(r rune) bool { return r == 0x130 || r == 0x3A3 || (r >= 0x1F88 && r <= 0x1FFC) }

// Full uppercase mappings Python applies to Latin text (SpecialCasing).
var upperExpansions = map[rune]string{0xDF: "SS", 0xFB00: "FF", 0xFB01: "FI", 0xFB02: "FL", 0xFB03: "FFI", 0xFB04: "FFL",
	0xFB05: "ST", 0xFB06: "ST"}

func pythonUpper(text string) (string, error) {
	var out strings.Builder
	for _, r := range text {
		if expansion, ok := upperExpansions[r]; ok {
			out.WriteString(expansion)
			continue
		}
		if specialUpper(r) {
			return "", unsupported("upper() of %U", r)
		}
		out.WriteRune(unicode.ToUpper(r))
	}
	return out.String(), nil
}

func pythonLower(text string) (string, error) {
	for _, r := range text {
		if specialLower(r) {
			return "", unsupported("lower() of %U", r)
		}
	}
	return strings.Map(unicode.ToLower, text), nil
}

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
