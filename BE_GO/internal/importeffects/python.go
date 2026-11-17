package importeffects

import (
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// DjangoError is a Python exception Django raises while the ORM writes an
// archive value. Message is str(exc), which import_all's `except Exception`
// stores as the task's error (and the restore's failure path keeps as the
// generic message). Class is the exception's type name. It is not a refusal:
// Django raises it for the same input.
type DjangoError struct {
	Class   string
	Message string
}

func (e *DjangoError) Error() string { return e.Message }

func djangoError(message string) error { return &DjangoError{Class: "ValueError", Message: message} }

func typeError(message string) error { return &DjangoError{Class: "TypeError", Message: message} }

// databaseError is django.db.DatabaseError, raised by save_base when an
// UPDATE matches no row.
func databaseError(message string) error {
	return &DjangoError{Class: "DatabaseError", Message: message}
}

// validationError is str(ValidationError(text)): the repr of the one-item
// message list ("['...']").
func validationError(text string) error {
	return &DjangoError{Class: "ValidationError", Message: "[" + dto.PythonStringRepr(text) + "]"}
}

// pyTypeName is type(value).__name__ for an in-memory attribute.
func pyTypeName(v Value) string {
	switch v.Kind {
	case None:
		return "NoneType"
	case Str:
		return "str"
	case Int:
		return "int"
	case Bool:
		return "bool"
	case Float:
		return "float"
	case Decimal:
		return "Decimal"
	case UUID:
		return "UUID"
	case DateTime:
		return "datetime.datetime"
	case Date:
		return "datetime.date"
	case JSON:
		switch v.J.(type) {
		case domain.RequestObject:
			return "dict"
		case []any:
			return "list"
		}
	}
	return "object"
}

// Truthy is bool(value) for callers outside the package.
func Truthy(v Value) bool { return pyTruthy(v) }

// pyTruthy is bool(value).
func pyTruthy(v Value) bool {
	switch v.Kind {
	case None:
		return false
	case Str:
		return v.S != ""
	case Int:
		return strings.TrimLeft(v.S, "0-") != ""
	case Bool:
		return v.B
	case Float:
		return v.S != "0.0" && v.S != "-0.0"
	case Decimal:
		return !decimalIsZero(v.S)
	case UUID, DateTime, Date:
		return true
	case JSON:
		switch container := v.J.(type) {
		case domain.RequestObject:
			return len(container) > 0
		case []any:
			return len(container) > 0
		}
	}
	return true
}

func decimalIsZero(text string) bool {
	decimal, err := parseDecimalString(text)
	if err != nil || decimal.special != "" {
		return false
	}
	return strings.Trim(decimal.digits, "0") == ""
}

// pyIntText is str(value) for a Python int (CPython's 4300-digit limit).
func pyIntText(digits string) (string, error) {
	if len(strings.TrimPrefix(digits, "-")) > maxIntStringDigits {
		return "", djangoError("Exceeds the limit (4300 digits) for integer string conversion; use sys.set_int_max_str_digits() to increase the limit")
	}
	return digits, nil
}

const maxIntStringDigits = 4300

// pyIntFromText is int(text) for a str, including Python's digit limit.
func pyIntFromText(text string) (string, error) {
	runes := []rune(text)
	start, end := 0, len(runes)
	for start < end && unicode.IsSpace(runes[start]) {
		start++
	}
	for end > start && unicode.IsSpace(runes[end-1]) {
		end--
	}
	digits := make([]byte, 0, end-start)
	sign := ""
	if start < end && (runes[start] == '+' || runes[start] == '-') {
		if runes[start] == '-' {
			sign = "-"
		}
		start++
	}
	count := 0
	lastUnderscore := false
	for _, r := range runes[start:end] {
		if r == '_' {
			// int() accepts single underscores between digits only.
			if count == 0 || lastUnderscore {
				return "", djangoError("invalid literal for int() with base 10: " + dto.PythonStringRepr(text))
			}
			lastUnderscore = true
			continue
		}
		lastUnderscore = false
		digit, ok := pyDecimalDigit(r)
		if !ok {
			return "", djangoError("invalid literal for int() with base 10: " + dto.PythonStringRepr(text))
		}
		digits = append(digits, byte('0'+digit))
		count++
	}
	if count == 0 || lastUnderscore {
		return "", djangoError("invalid literal for int() with base 10: " + dto.PythonStringRepr(text))
	}
	if count > maxIntStringDigits {
		return "", djangoError(fmt.Sprintf("Exceeds the limit (4300 digits) for integer string conversion: value has %d digits; use sys.set_int_max_str_digits() to increase the limit", count))
	}
	trimmed := strings.TrimLeft(string(digits), "0")
	if trimmed == "" {
		return "0", nil
	}
	return sign + trimmed, nil
}

// pyDecimalDigit is the value of a Unicode decimal digit (str.isdecimal).
func pyDecimalDigit(r rune) (int, bool) {
	if r >= '0' && r <= '9' {
		return int(r - '0'), true
	}
	for _, table := range unicode.Nd.R16 {
		if uint32(r) >= uint32(table.Lo) && uint32(r) <= uint32(table.Hi) && table.Stride == 1 {
			return int(uint32(r) - uint32(table.Lo)), true
		}
	}
	for _, table := range unicode.Nd.R32 {
		if uint32(r) >= table.Lo && uint32(r) <= table.Hi && table.Stride == 1 {
			return int(uint32(r) - table.Lo), true
		}
	}
	return 0, false
}

// pyIntFromFloat is int(float).
func pyIntFromFloat(f float64) (string, error) {
	if math.IsNaN(f) {
		return "", djangoError("cannot convert float NaN to integer")
	}
	if math.IsInf(f, 0) {
		return "", &DjangoError{Class: "OverflowError", Message: "cannot convert float infinity to integer"}
	}
	integer, _ := new(big.Float).SetFloat64(f).Int(nil)
	return pyIntText(integer.String())
}

// pyIntFromValue is int(value) for an in-memory attribute.
func pyIntFromValue(v Value) (string, error) {
	switch v.Kind {
	case Int:
		return pyIntText(v.S)
	case Bool:
		if v.B {
			return "1", nil
		}
		return "0", nil
	case Float:
		f, err := pyFloatValue(v.S)
		if err != nil {
			return "", err
		}
		return pyIntFromFloat(f)
	case Str:
		return pyIntFromText(v.S)
	case Decimal:
		d, err := parseDecimalString(v.S)
		if err != nil {
			return "", err
		}
		return d.Integer()
	}
	return "", typeError(fmt.Sprintf("int() argument must be a string, a bytes-like object or a real number, not '%s'", pyTypeName(v)))
}

// pyFloatValue is the float64 a Float value holds.
func pyFloatValue(text string) (float64, error) {
	switch text {
	case "inf":
		return math.Inf(1), nil
	case "-inf":
		return math.Inf(-1), nil
	case "nan":
		return math.NaN(), nil
	}
	f, err := strconv.ParseFloat(text, 64)
	if err != nil {
		return 0, err
	}
	return f, nil
}

// pyFloatText is repr(float) and str(float).
func pyFloatText(f float64) string {
	switch {
	case math.IsInf(f, 1):
		return "inf"
	case math.IsInf(f, -1):
		return "-inf"
	case math.IsNaN(f):
		return "nan"
	}
	return dto.PythonFloatStr(f)
}

// decimalValue is a Python decimal.Decimal: a signed coefficient, a decimal
// exponent, or one of the special values.
type decimalValue struct {
	negative bool
	digits   string // coefficient, no leading zeros ("0" for zero)
	exp      int64
	special  string // "", "NaN", "sNaN", "Infinity"
}

const decimalMaxExp = int64(999999999999999999)

var (
	errDecimalSyntax     = djangoError("[<class 'decimal.ConversionSyntax'>]")
	errDecimalInvalid    = djangoError("[<class 'decimal.InvalidOperation'>]")
	errDecimalNotANumber = func(v Value) error {
		return validationError(fmt.Sprintf("Il valore \"%s\" deve essere un numero decimale.", mustText(v)))
	}
)

// parseDecimalString is decimal.Decimal(text).
func parseDecimalString(text string) (decimalValue, error) {
	s := strings.TrimSpace(text)
	s = strings.ReplaceAll(s, "_", "")
	negative := false
	if strings.HasPrefix(s, "+") || strings.HasPrefix(s, "-") {
		negative = s[0] == '-'
		s = s[1:]
	}
	lower := strings.ToLower(s)
	switch lower {
	case "inf", "infinity":
		return decimalValue{negative: negative, special: "Infinity"}, nil
	case "nan":
		return decimalValue{negative: negative, special: "NaN"}, nil
	case "snan":
		return decimalValue{negative: negative, special: "sNaN"}, nil
	}
	mantissa, exponentPart := s, ""
	if index := strings.IndexAny(s, "eE"); index >= 0 {
		mantissa, exponentPart = s[:index], s[index+1:]
	}
	whole, fraction := mantissa, ""
	if index := strings.IndexByte(mantissa, '.'); index >= 0 {
		whole, fraction = mantissa[:index], mantissa[index+1:]
	}
	if whole == "" && fraction == "" {
		return decimalValue{}, errDecimalSyntax
	}
	digits := make([]byte, 0, len(whole)+len(fraction))
	for _, r := range whole + fraction {
		digit, ok := pyDecimalDigit(r)
		if !ok {
			return decimalValue{}, errDecimalSyntax
		}
		digits = append(digits, byte('0'+digit))
	}
	exponent := int64(0)
	if exponentPart != "" {
		sign := int64(1)
		if strings.HasPrefix(exponentPart, "+") || strings.HasPrefix(exponentPart, "-") {
			if exponentPart[0] == '-' {
				sign = -1
			}
			exponentPart = exponentPart[1:]
		}
		if exponentPart == "" {
			return decimalValue{}, errDecimalSyntax
		}
		value := new(big.Int)
		for _, r := range exponentPart {
			digit, ok := pyDecimalDigit(r)
			if !ok {
				return decimalValue{}, errDecimalSyntax
			}
			value.Mul(value, big.NewInt(10))
			value.Add(value, big.NewInt(int64(digit)))
		}
		if !value.IsInt64() || value.Int64() > decimalMaxExp {
			return decimalValue{}, errDecimalInvalid
		}
		exponent = sign * value.Int64()
	}
	exponent -= int64(len(fraction))
	if exponent < -decimalMaxExp || exponent > decimalMaxExp {
		return decimalValue{}, errDecimalInvalid
	}
	trimmed := strings.TrimLeft(string(digits), "0")
	if trimmed == "" {
		trimmed = "0"
	}
	return decimalValue{negative: negative, digits: trimmed, exp: exponent}, nil
}

// decimalFromFloat is decimal.Decimal(float): the exact binary value, with
// trailing zeros removed.
func decimalFromFloat(f float64) (decimalValue, error) {
	if math.IsNaN(f) {
		return decimalValue{special: "NaN"}, nil
	}
	if math.IsInf(f, 0) {
		return decimalValue{negative: f < 0, special: "Infinity"}, nil
	}
	if f == 0 {
		return decimalValue{negative: math.Signbit(f), digits: "0"}, nil
	}
	rational := new(big.Rat).SetFloat64(f)
	denominator := new(big.Int).Set(rational.Denom())
	shift := int64(0)
	two := big.NewInt(2)
	remainder := new(big.Int)
	for denominator.Cmp(big.NewInt(1)) > 0 {
		denominator.DivMod(denominator, two, remainder)
		if remainder.Sign() != 0 {
			return decimalValue{}, errDecimalSyntax
		}
		shift++
	}
	// 1/2**shift = 5**shift / 10**shift.
	scaled := new(big.Int).Set(rational.Num())
	scaled.Mul(scaled, new(big.Int).Exp(big.NewInt(5), big.NewInt(shift), nil))
	negative := scaled.Sign() < 0
	digits := new(big.Int).Abs(scaled).String()
	for len(digits) > 1 && strings.HasSuffix(digits, "0") {
		digits = digits[:len(digits)-1]
		shift--
	}
	return decimalValue{negative: negative, digits: digits, exp: -shift}, nil
}

// String is str(Decimal).
func (d decimalValue) String() string {
	if d.special != "" {
		if d.negative {
			return "-" + d.special
		}
		return d.special
	}
	digits := d.digits
	if digits == "" {
		digits = "0"
	}
	adjusted := d.exp + int64(len(digits)) - 1
	sign := ""
	if d.negative {
		sign = "-"
	}
	if d.exp <= 0 && adjusted >= -6 {
		point := int(d.exp) + len(digits)
		switch {
		case point <= 0:
			return sign + "0." + strings.Repeat("0", -point) + digits
		case point >= len(digits):
			return sign + digits
		default:
			return sign + digits[:point] + "." + digits[point:]
		}
	}
	mantissa := digits[:1]
	if len(digits) > 1 {
		mantissa += "." + digits[1:]
	}
	exponentSign := "+"
	exponent := adjusted
	if exponent < 0 {
		exponentSign, exponent = "-", -exponent
	}
	return sign + mantissa + "E" + exponentSign + strconv.FormatInt(exponent, 10)
}

// Integer is int(Decimal), truncating toward zero.
func (d decimalValue) Integer() (string, error) {
	if d.special != "" {
		if d.special == "Infinity" {
			return "", djangoError("cannot convert Infinity to integer")
		}
		return "", djangoError("cannot convert NaN to integer")
	}
	if d.exp >= 0 {
		digits := d.digits + strings.Repeat("0", int(d.exp))
		return pyIntText(withSign(digits, d.negative))
	}
	digits := d.digits
	drop := int(-d.exp)
	if drop >= len(digits) {
		return "0", nil
	}
	digits = digits[:len(digits)-drop]
	trimmed := strings.TrimLeft(digits, "0")
	if trimmed == "" {
		return "0", nil
	}
	return withSign(trimmed, d.negative), nil
}

func withSign(digits string, negative bool) string {
	if negative && digits != "0" {
		return "-" + digits
	}
	return digits
}

// decimalFromValue is decimal.Decimal(value).
func decimalFromValue(v Value) (decimalValue, error) {
	switch v.Kind {
	case Str:
		return parseDecimalString(v.S)
	case Int:
		return decimalValue{digits: strings.TrimPrefix(v.S, "-"), negative: strings.HasPrefix(v.S, "-"), exp: 0}, nil
	case Bool:
		if v.B {
			return decimalValue{digits: "1"}, nil
		}
		return decimalValue{digits: "0"}, nil
	case Float:
		f, err := pyFloatValue(v.S)
		if err != nil {
			return decimalValue{}, err
		}
		return decimalFromFloat(f)
	case Decimal:
		return parseDecimalString(v.S)
	}
	return decimalValue{}, djangoError(fmt.Sprintf("conversion from %s to Decimal is not supported", pyTypeName(v)))
}

// mustText is str(value) for an error message.
func mustText(v Value) string {
	text, err := v.Text()
	if err != nil {
		return v.S
	}
	return text
}
