package pdfexport

import (
	"math"
	"math/big"
	"strconv"
	"strings"
	"unicode"
)

// The attribute values of paraparser.py. Every conversion answers one of
// three things: a value, an exception reportlab raises (the export fails:
// ErrLayout) or an input whose Python semantics this package does not
// reproduce (ErrNotPorted: nan and infinity, and non-digit non-ASCII text in a
// number). The colours are in color.go and tocolor.go.

// pyNum is a number of the paragraph markup: a Python int (its exact decimal
// digits and their float64 value) or a Python float. Python keeps ints exact
// until an operation mixes them with a float, and the layout converts to
// float64 where reportlab does. The zero value is the int 0, as the numbers a
// ParagraphStyle leaves unset are.
type pyNum struct {
	f       float64
	digits  string // non-empty: the non-zero exact int, f is float(int)
	isFloat bool
}

// numWide is the pyNum of a Python int.
func numWide(v *big.Int) pyNum {
	if v.Sign() == 0 {
		return pyNum{}
	}
	f, _ := new(big.Rat).SetInt(v).Float64()
	return pyNum{f: f, digits: v.String()}
}

// numInt is the pyNum of an int of the documents.
func numInt(v int64) pyNum { return numWide(big.NewInt(v)) }

// numFloat is the pyNum of a float.
func numFloat(v float64) pyNum { return pyNum{f: v, isFloat: true} }

func (n pyNum) isInt() bool { return !n.isFloat }

// big is the exact integer of the pyNum: nil for a float.
func (n pyNum) big() *big.Int {
	if n.isFloat {
		return nil
	}
	if n.digits == "" {
		return new(big.Int)
	}
	value, _ := new(big.Int).SetString(n.digits, 10)
	return value
}

// toFloat is float(n): ok false where Python raises OverflowError, for an int
// beyond the range of float64.
func (n pyNum) toFloat() (float64, bool) {
	if n.isInt() && math.IsInf(n.f, 0) {
		return n.f, false
	}
	return n.f, true
}

// float is float(n) where the layout uses the number: an int too large for a
// float fails the build as reportlab's OverflowError does.
func (n pyNum) float() float64 {
	value, ok := n.toFloat()
	if !ok {
		panic(paraFailure{"int too large to convert to float"})
	}
	return value
}

// truthy is bool(n): an int or float that is not zero (a NaN is true).
func (n pyNum) truthy() bool {
	if n.isInt() {
		return n.digits != ""
	}
	return n.f != 0
}

// numAdd is a+b: ints stay exact, a float makes the sum a float.
func numAdd(a, b pyNum) (pyNum, bool) {
	if a.isInt() && b.isInt() {
		return numWide(new(big.Int).Add(a.big(), b.big())), true
	}
	af, ok := a.toFloat()
	if !ok {
		return pyNum{}, false
	}
	bf, ok := b.toFloat()
	if !ok {
		return pyNum{}, false
	}
	return numFloat(af + bf), true
}

// numSub is a-b.
func numSub(a, b pyNum) (pyNum, bool) {
	if a.isInt() && b.isInt() {
		return numWide(new(big.Int).Sub(a.big(), b.big())), true
	}
	af, ok := a.toFloat()
	if !ok {
		return pyNum{}, false
	}
	bf, ok := b.toFloat()
	if !ok {
		return pyNum{}, false
	}
	return numFloat(af - bf), true
}

// numMul is a*b.
func numMul(a, b pyNum) (pyNum, bool) {
	if a.isInt() && b.isInt() {
		return numWide(new(big.Int).Mul(a.big(), b.big())), true
	}
	af, ok := a.toFloat()
	if !ok {
		return pyNum{}, false
	}
	bf, ok := b.toFloat()
	if !ok {
		return pyNum{}, false
	}
	return numFloat(af * bf), true
}

// floatRat is the exact value of a finite float, or nil for a NaN or an
// infinity.
func floatRat(f float64) *big.Rat {
	if math.IsNaN(f) || math.IsInf(f, 0) {
		return nil
	}
	return new(big.Rat).SetFloat64(f)
}

// numLess is a<b of Python: an int and a float compare by exact value, and a
// NaN is less than nothing.
func numLess(a, b pyNum) bool {
	switch {
	case a.isInt() && b.isInt():
		return a.big().Cmp(b.big()) < 0
	case !a.isInt() && !b.isInt():
		return a.f < b.f
	case a.isInt():
		if math.IsNaN(b.f) {
			return false
		}
		if math.IsInf(b.f, 0) {
			return b.f > 0
		}
		return new(big.Rat).SetInt(a.big()).Cmp(floatRat(b.f)) < 0
	default:
		if math.IsNaN(a.f) {
			return false
		}
		if math.IsInf(a.f, 0) {
			return a.f < 0
		}
		return floatRat(a.f).Cmp(new(big.Rat).SetInt(b.big())) < 0
	}
}

// numEqual is a==b of Python, exact between an int and a float.
func numEqual(a, b pyNum) bool {
	switch {
	case a.isInt() && b.isInt():
		return a.big().Cmp(b.big()) == 0
	case !a.isInt() && !b.isInt():
		return a.f == b.f
	case a.isInt():
		if math.IsNaN(b.f) || math.IsInf(b.f, 0) {
			return false
		}
		return new(big.Rat).SetInt(a.big()).Cmp(floatRat(b.f)) == 0
	default:
		if math.IsNaN(a.f) || math.IsInf(a.f, 0) {
			return false
		}
		return floatRat(a.f).Cmp(new(big.Rat).SetInt(b.big())) == 0
	}
}

// numMin is Python's min(a, b): b replaces a only when it compares less.
func numMin(a, b pyNum) pyNum {
	if numLess(b, a) {
		return b
	}
	return a
}

// numMax is Python's max(a, b): b replaces a only when it compares greater.
func numMax(a, b pyNum) pyNum {
	if numLess(a, b) {
		return b
	}
	return a
}

// pyMin is Python's min(a, b) over floats: the first argument stays unless the
// second compares less, so a NaN first argument is the result.
func pyMin(a, b float64) float64 {
	if b < a {
		return b
	}
	return a
}

// pyMax is Python's max(a, b) over floats.
func pyMax(a, b float64) float64 {
	if b > a {
		return b
	}
	return a
}

// pyMinN is Python's min(a, b, ...) over the values of a tuple.
func pyMinN(values ...float64) float64 {
	result := values[0]
	for _, value := range values[1:] {
		if value < result {
			result = value
		}
	}
	return result
}

// pyMaxN is Python's max(a, b, ...) over the values of a tuple.
func pyMaxN(values ...float64) float64 {
	result := values[0]
	for _, value := range values[1:] {
		if value > result {
			result = value
		}
	}
	return result
}

type attrStatus int

const (
	attrOK attrStatus = iota
	attrInvalid
	attrUnsupported
)

func pySpaceByte(b byte) bool {
	switch b {
	case ' ', '\t', '\n', '\v', '\f', '\r', 0x1c, 0x1d, 0x1e, 0x1f:
		return true
	}
	return false
}

func pyTrimASCII(s string) string {
	start, end := 0, len(s)
	for start < end && pySpaceByte(s[start]) {
		start++
	}
	for end > start && pySpaceByte(s[end-1]) {
		end--
	}
	return s[start:end]
}

// pyTrimNumber is the trimming of int() and float(): the ASCII whitespace
// CPython's parser skips. The non-ASCII spaces were turned into ' ' by
// normalizeDigits, while \x1c-\x1f are not whitespace to int().
func pyTrimNumber(s string) string {
	start, end := 0, len(s)
	for start < end && (s[start] == ' ' || s[start] == '\t' || s[start] == '\n' || s[start] == '\v' || s[start] == '\f' || s[start] == '\r') {
		start++
	}
	for end > start && (s[end-1] == ' ' || s[end-1] == '\t' || s[end-1] == '\n' || s[end-1] == '\v' || s[end-1] == '\f' || s[end-1] == '\r') {
		end--
	}
	return s[start:end]
}

func asciiOnly(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] >= 0x80 {
			return false
		}
	}
	return true
}

// normalizeDigits is the transform CPython applies to the string of int() and
// float(): every decimal digit of another script (unicode.Nd) becomes its
// ASCII digit and every Unicode space becomes ' '; a character that is neither
// makes the conversion fail (ok false, a ValueError).
func normalizeDigits(s string) (string, bool) {
	ascii := true
	for i := 0; i < len(s); i++ {
		if s[i] >= 0x80 {
			ascii = false
			break
		}
	}
	if ascii {
		return s, true
	}
	var out strings.Builder
	for _, r := range s {
		switch {
		case r < 0x80:
			out.WriteRune(r)
		case unicode.Is(unicode.Nd, r):
			out.WriteByte(digitValue(r))
		case strings.ContainsRune(pySpaces, r):
			out.WriteByte(' ')
		default:
			return "", false
		}
	}
	return out.String(), true
}

// digitValue is the ASCII digit of a unicode.Nd rune: an Nd range starts at
// the zero of its script.
func digitValue(r rune) byte {
	for _, entry := range unicode.Nd.R16 {
		if r >= rune(entry.Lo) && r <= rune(entry.Hi) {
			return byte('0' + (r - rune(entry.Lo)))
		}
	}
	for _, entry := range unicode.Nd.R32 {
		if r >= rune(entry.Lo) && r <= rune(entry.Hi) {
			return byte('0' + (r - rune(entry.Lo)))
		}
	}
	return 0
}

// digitRun reads digits of a base with single underscores between them from
// i: the digits without underscores and the end, or ok false for a malformed
// run.
func digitRun(s string, i, base int) (string, int, bool) {
	digit := func(c byte) bool {
		if c >= '0' && c <= '9' {
			return int(c-'0') < base
		}
		return base == 16 && (c >= 'a' && c <= 'f' || c >= 'A' && c <= 'F')
	}
	var out []byte
	for i < len(s) {
		c := s[i]
		switch {
		case digit(c):
			out = append(out, c)
			i++
		case c == '_':
			if len(out) == 0 || i+1 >= len(s) || !digit(s[i+1]) {
				return "", i, false
			}
			i++
		default:
			return string(out), i, true
		}
	}
	return string(out), i, true
}

// pyInt is int(s, base) of CPython for base 10 and 16: an exact integer, the
// ValueError of int() as attrInvalid. A decimal string of more than 4300
// digits is a ValueError too (the limit sys.set_int_max_str_digits sets),
// while base 16 has none.
func pyInt(text string, base int) (*big.Int, attrStatus) {
	s, ok := normalizeDigits(text)
	if !ok {
		return nil, attrInvalid
	}
	s = pyTrimNumber(s)
	neg := false
	if s != "" && (s[0] == '+' || s[0] == '-') {
		neg = s[0] == '-'
		s = s[1:]
	}
	if base == 16 && len(s) >= 2 && s[0] == '0' && (s[1] == 'x' || s[1] == 'X') {
		// int() reads the base prefix, and one underscore may follow it.
		s = s[2:]
		if strings.HasPrefix(s, "_") {
			s = s[1:]
		}
	}
	digits, end, ok := digitRun(s, 0, base)
	if !ok || digits == "" || end != len(s) {
		return nil, attrInvalid
	}
	if base != 2 && base != 4 && base != 8 && base != 16 && base != 32 && len(digits) > 4300 {
		return nil, attrInvalid
	}
	value, ok := new(big.Int).SetString(digits, base)
	if !ok {
		return nil, attrInvalid
	}
	if neg {
		value.Neg(value)
	}
	return value, attrOK
}

// pyFloat is float(s): a decimal literal with single underscores between its
// digits, inf/infinity and nan in any case with a sign, and an infinity for a
// literal beyond the range of float64 (float('1e400') is inf, no exception).
func pyFloat(text string) (float64, attrStatus) {
	s, ok := normalizeDigits(text)
	if !ok {
		return 0, attrInvalid
	}
	s = pyTrimNumber(s)
	body := s
	negative := false
	if body != "" && (body[0] == '+' || body[0] == '-') {
		negative = body[0] == '-'
		body = body[1:]
	}
	switch strings.ToLower(body) {
	case "inf", "infinity":
		return math.Inf(signOf(negative)), attrOK
	case "nan":
		return math.NaN(), attrOK
	}
	intPart, i, ok := digitRun(body, 0, 10)
	if !ok {
		return 0, attrInvalid
	}
	fracPart := ""
	if i < len(body) && body[i] == '.' {
		fracPart, i, ok = digitRun(body, i+1, 10)
		if !ok {
			return 0, attrInvalid
		}
	}
	if intPart == "" && fracPart == "" {
		return 0, attrInvalid
	}
	exponent := ""
	if i < len(body) && (body[i] == 'e' || body[i] == 'E') {
		j := i + 1
		sign := ""
		if j < len(body) && (body[j] == '+' || body[j] == '-') {
			sign = string(body[j])
			j++
		}
		var digits string
		digits, i, ok = digitRun(body, j, 10)
		if !ok || digits == "" {
			return 0, attrInvalid
		}
		exponent = "e" + sign + digits
	}
	if i != len(body) {
		return 0, attrInvalid
	}
	if intPart == "" {
		intPart = "0"
	}
	if fracPart == "" {
		fracPart = "0"
	}
	number := intPart + "." + fracPart + exponent
	if negative {
		number = "-" + number
	}
	value, err := strconv.ParseFloat(number, 64)
	if err != nil {
		// A literal beyond the range of float64 is an infinity: Python's
		// float() answers it without an exception.
		if rangeError, ok := err.(*strconv.NumError); !ok || rangeError.Err != strconv.ErrRange {
			return 0, attrInvalid
		}
	}
	return value, attrOK
}

// signOf is the sign of a number: 1 or -1.
func signOf(negative bool) int {
	if negative {
		return -1
	}
	return 1
}

// numVal is a number of _num: a value in points, or a relative one.
type numVal struct {
	v   pyNum
	rel bool
}

// paraConvNum is _convnum(s, unit, allowRelative).
func paraConvNum(s string, unit pyNum, allowRelative bool) (numVal, attrStatus) {
	if s == "" {
		return numVal{}, attrInvalid // IndexError
	}
	if (s[0] == '+' || s[0] == '-') && allowRelative {
		if n, st := pyInt(s, 10); st != attrInvalid {
			v, ok := numMul(numWide(n), unit)
			if !ok {
				return numVal{}, attrInvalid
			}
			return numVal{v, true}, st
		}
		f, st := pyFloat(s)
		v, ok := numMul(numFloat(f), unit)
		if !ok {
			return numVal{}, attrInvalid
		}
		return numVal{v, true}, st
	}
	if n, st := pyInt(s, 10); st != attrInvalid {
		v, ok := numMul(numWide(n), unit)
		if !ok {
			return numVal{}, attrInvalid
		}
		return numVal{v, false}, st
	}
	f, st := pyFloat(s)
	v, ok := numMul(numFloat(f), unit)
	if !ok {
		return numVal{}, attrInvalid
	}
	return numVal{v, false}, st
}

// The units of reportlab.lib.units, computed with float64 operations (not
// exact constants); 'pt' and the default unit are the int 1, so a value
// without a unit that int() reads stays an int.
var (
	unitInch = numFloat(72.0)
	unitCM   = numFloat(divide(72.0, 2.54))
	unitMM   = numFloat(divide(72.0, 2.54) * 0.1)
	unitPica = numFloat(12.0)
	unitNone = numInt(1)
)

func divide(a, b float64) float64 { return a / b }

// paraNum is _num(s, allowRelative=...): a number with an optional unit. The
// unit is the last one the regex `^\s*(.*)(i|in|cm|mm|pt|pica)\s*$` finds,
// whose leading and trailing spaces are Unicode ones and whose group cannot
// cross a line break.
func paraNum(text string, allowRelative bool) (numVal, attrStatus) {
	body := pyTrimUnicode(text)
	if !strings.Contains(body, "\n") {
		for _, unit := range []struct {
			suffix string
			value  pyNum
		}{{"i", unitInch}, {"in", unitInch}, {"cm", unitCM}, {"mm", unitMM}, {"pt", unitNone}, {"pica", unitPica}} {
			if strings.HasSuffix(body, unit.suffix) {
				return paraConvNum(body[:len(body)-len(unit.suffix)], unit.value, allowRelative)
			}
		}
	}
	return paraConvNum(text, unitNone, allowRelative)
}

// paraBool is _bool: a value that is not one of its words is an exception.
func paraBool(s string) (bool, attrStatus) {
	if !asciiOnly(s) {
		return false, attrInvalid
	}
	switch strings.ToLower(s) {
	case "true", "1", "yes":
		return true, attrOK
	case "false", "0", "no":
		return false, attrOK
	}
	return false, attrInvalid
}
