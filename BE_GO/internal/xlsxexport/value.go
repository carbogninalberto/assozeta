// Package xlsxexport writes the XLSX workbooks Django's exports build with
// pandas + openpyxl, pandas + xlsxwriter and plain openpyxl (pandas 2.2.3,
// openpyxl 3.1.5 without lxml, XlsxWriter 3.2.2), part for part: the same zip
// entries in the same order with the same compression method and flags, and
// byte-identical XML except the docProps/core.xml creation and modification
// times (see docs/architecture/go-migration/xlsx-exports.md). The golden
// workbooks in testdata come from Django's own code
// (TESTS/fixtures/generate_xlsx_golden.py).
package xlsxexport

import (
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrDjango is a workbook Django fails to build (get_excel_base64 logs and
// returns None, or the library raises): the views answer 500.
var ErrDjango = errors.New("xlsxexport: Django fails to build this workbook")

// ErrNotPorted is an input whose workbook this package does not reproduce:
// callers refuse it before any write.
var ErrNotPorted = refusal.New("xlsxexport: workbook input not ported")

// Kind is the Python type of a cell value as the view hands it to pandas.
type Kind uint8

const (
	None    Kind = iota // None
	Str                 // str
	Int                 // int (int64 range)
	Float               // float
	Decimal             // decimal.Decimal (Text is str(value))
	Bool                // bool
	Object              // a list or dict: Text is str(value) (its repr)
)

// Value is one Python value.
type Value struct {
	Kind  Kind
	Text  string
	Int   int64
	Float float64
	Bool  bool
}

func NoneValue() Value                { return Value{Kind: None} }
func StrValue(text string) Value      { return Value{Kind: Str, Text: text} }
func IntValue(number int64) Value     { return Value{Kind: Int, Int: number} }
func FloatValue(number float64) Value { return Value{Kind: Float, Float: number} }

// DecimalValue is a Decimal whose str() is text (a finite value).
func DecimalValue(text string) Value { return Value{Kind: Decimal, Text: text} }
func BoolValue(value bool) Value     { return Value{Kind: Bool, Bool: value} }

// ObjectValue is a list or dict whose str() (repr) is text.
func ObjectValue(repr string) Value { return Value{Kind: Object, Text: repr} }

// OptionalStr is a str, or None for nil.
func OptionalStr(text *string) Value {
	if text == nil {
		return NoneValue()
	}
	return StrValue(*text)
}

// PythonStr is str() of the value.
func (value Value) PythonStr() string { return pythonStrOf(value) }
