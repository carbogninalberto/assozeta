package pandasxlsx

import (
	"errors"
	"math"
	"math/big"
	"runtime"
	"strconv"
	"strings"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// powersOfTen is precise_xstrtod's table of 1e0 to 1e308.
var powersOfTen = func() [309]float64 {
	var table [309]float64
	for exponent := range table {
		table[exponent], _ = strconv.ParseFloat("1e"+strconv.Itoa(exponent), 64)
	}
	return table
}()

var (
	minInt64  = big.NewInt(math.MinInt64)
	maxInt64  = big.NewInt(math.MaxInt64)
	maxUint64 = new(big.Int).SetUint64(math.MaxUint64)
)

// isASCIISpace is the C tokenizer's isspace_ascii.
func isASCIISpace(c byte) bool { return c == ' ' || c >= '\t' && c <= '\r' }

func isASCIIDigit(c byte) bool { return c >= '0' && c <= '9' }

// numeric is what maybe_convert_numeric reads in one str.
type numeric struct {
	// integer: the text is whitespace, a sign and digits, read by int().
	integer bool
	i       *big.Int
	// f is the float the tokenizer's precise_xstrtod gives.
	f float64
}

// floatAccumulation is how the pandas wheel of this platform accumulates the
// digits of a float: 'number * 10 + digit' is contracted into a fused
// multiply-add by the macOS arm64 wheel and not by the Linux wheels. Measured
// with pandas 2.2.3 on '3.14159265358979323846': 0x400921fb54442d18 on
// darwin/arm64, 0x400921fb54442d17 on linux/amd64 and linux/arm64; beyond
// 2**53 the two differ. Same measurement and rule as
// internal/pandascsv/frame.go and internal/xlsxexport/pandas.go, which convert
// the same texts with the same pandas function.
var floatAccumulation = map[string]bool{
	"darwin/arm64": true,
	"linux/amd64":  false,
	"linux/arm64":  false,
}

// errFloatPlatform is an application state: the value of a text here depends
// on whether the pandas wheel contracted the digit accumulation, and this
// platform's wheel was never measured (images are built for Linux only).
var errFloatPlatform = refusal.AppState("pandasxlsx: float value depends on the pandas build")

// strtod is precise_xstrtod followed by to_double's end-of-text check, read
// as the pandas wheel of this platform compiles it: the fused or the plain
// accumulation (floatAccumulation). ok is false for text the tokenizer does
// not read as a number. The error is errFloatPlatform where the two
// accumulations differ on a platform whose wheel was never measured.
func strtod(text string) (value float64, maybeInt, ok bool, err error) {
	fused, plain, maybeInt, ok := preciseStrtod(text)
	if !ok {
		return 0, false, false, nil
	}
	if fused == plain {
		return fused, maybeInt, true, nil
	}
	contracted, measured := floatAccumulation[runtime.GOOS+"/"+runtime.GOARCH]
	if !measured {
		return 0, false, false, errFloatPlatform
	}
	if contracted {
		return fused, maybeInt, true, nil
	}
	return plain, maybeInt, true, nil
}

// preciseStrtod is the tokenizer's precise_xstrtod (decimal '.', exponent
// 'e', no thousands separator, trailing whitespace skipped) as pandas'
// tokenizer.c computes it. The exponent is the C int it is accumulated in:
// an int32, wrapping like two's complement in its digits and in its sum
// (pandas 2.2.3: '1e4294967296' is 1.0, '1e2147483648' is 0.0,
// '1e-2147483649' is not a float). fused and plain are the two digit
// accumulations the wheels compile.
func preciseStrtod(text string) (fused, plain float64, maybeInt, ok bool) {
	at := func(index int) byte {
		if index < len(text) {
			return text[index]
		}
		return 0
	}
	const maxDigits = 17
	p := 0
	maybeInt = true
	for isASCIISpace(at(p)) {
		p++
	}
	negative := false
	switch at(p) {
	case '-':
		negative = true
		p++
	case '+':
		p++
	}
	number, contracted := 0.0, 0.0
	exponent, digits, decimals := int32(0), 0, int32(0)
	push := func(c byte) {
		digit := float64(c - '0')
		// The plain accumulation keeps the product and the sum two rounded
		// operations; the contracted one is a single fused multiply-add.
		number = float64(number*10) + digit
		contracted = math.FMA(contracted, 10, digit)
		digits++
	}
	for isASCIIDigit(at(p)) {
		if digits < maxDigits {
			push(at(p))
		} else {
			exponent++
		}
		p++
	}
	if at(p) == '.' {
		maybeInt = false
		p++
		for digits < maxDigits && isASCIIDigit(at(p)) {
			push(at(p))
			p++
			decimals++
		}
		if digits >= maxDigits {
			for isASCIIDigit(at(p)) {
				p++
			}
		}
		exponent -= decimals
	}
	if digits == 0 {
		return 0, 0, false, false
	}
	if negative {
		number, contracted = -number, -contracted
	}
	if at(p) == 'e' || at(p) == 'E' {
		maybeInt = false
		p++
		negative = false
		switch at(p) {
		case '-':
			negative = true
			p++
		case '+':
			p++
		}
		count, n := 0, int32(0)
		for ; count < maxDigits && isASCIIDigit(at(p)); p++ {
			n = n*10 + int32(at(p)-'0')
			count++
		}
		if negative {
			exponent -= n
		} else {
			exponent += n
		}
		if count == 0 {
			p-- // the exponent mark is not consumed
		}
	}
	switch {
	case exponent > 308:
		return 0, 0, false, false
	case exponent > 0:
		power := powersOfTen[exponent]
		number, contracted = number*power, contracted*power
	case exponent < -308:
		if exponent < -616 {
			number, contracted = 0, 0
		} else {
			first, second := powersOfTen[-308-exponent], powersOfTen[308]
			number, contracted = number/first/second, contracted/first/second
		}
	default:
		power := powersOfTen[-exponent]
		number, contracted = number/power, contracted/power
	}
	if math.IsInf(number, 0) || math.IsInf(contracted, 0) {
		return 0, 0, false, false
	}
	for isASCIISpace(at(p)) {
		p++
	}
	if p != len(text) {
		return 0, 0, false, false
	}
	return contracted, number, maybeInt, true
}

// numericText is floatify and the int() that follows it in
// maybe_convert_numeric, for a str that is not an NA value. ok is false where
// the conversion raises (text that is no number, an integer outside int64 and
// uint64). The error is errFloatPlatform on a platform whose wheel was never
// measured.
func numericText(text string) (numeric, bool, error) {
	if strings.IndexByte(text, 0) >= 0 {
		// XML carries no NUL (the decoder rejects it), so no cell text has one.
		return numeric{}, false, refusal.AppState("pandasxlsx: a text with a NUL byte")
	}
	value, maybeInt, ok, err := strtod(text)
	if err != nil {
		return numeric{}, false, err
	}
	if !ok {
		// The words floatify accepts: an optional sign and 'inf' or
		// 'infinity' in any case.
		word, sign := text, 1
		if word != "" && (word[0] == '+' || word[0] == '-') {
			if word[0] == '-' {
				sign = -1
			}
			word = word[1:]
		}
		if lowered := strings.ToLower(word); isASCII(word) && (lowered == "inf" || lowered == "infinity") {
			return numeric{f: math.Inf(sign)}, true, nil
		}
		return numeric{}, false, nil
	}
	if !maybeInt {
		return numeric{f: value}, true, nil
	}
	body := strings.TrimFunc(text, func(r rune) bool { return r < 0x80 && isASCIISpace(byte(r)) })
	if len(body) > 4300 {
		return numeric{}, false, nil // int() refuses more than 4300 digits
	}
	integer, parsed := new(big.Int).SetString(strings.TrimPrefix(body, "+"), 10)
	if !parsed {
		// preciseStrtod only reports an integer for a sign and ASCII digits.
		return numeric{}, false, refusal.AppState("pandasxlsx: a text that reads as an integer and does not parse")
	}
	if integer.Cmp(minInt64) < 0 || integer.Cmp(maxUint64) > 0 {
		return numeric{}, false, nil // 'Integer out of range.'
	}
	return numeric{integer: true, i: integer, f: value}, true, nil
}

// pythonSpace is str.isspace for the characters int() and float() strip.
func pythonSpace(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }

// decimalDigit is the value of a Unicode decimal digit: Python's \d and
// str.isdecimal (the category Nd). Every Nd block starts at its zero.
func decimalDigit(r rune) (int, bool) {
	if r >= '0' && r <= '9' {
		return int(r - '0'), true
	}
	if !unicode.Is(unicode.Nd, r) {
		return 0, false
	}
	for _, entry := range unicode.Nd.R16 {
		if r >= rune(entry.Lo) && r <= rune(entry.Hi) {
			return int(r-rune(entry.Lo)) % 10, true
		}
	}
	for _, entry := range unicode.Nd.R32 {
		if r >= rune(entry.Lo) && r <= rune(entry.Hi) {
			return int(r-rune(entry.Lo)) % 10, true
		}
	}
	return 0, false
}

// digitText rewrites every decimal digit of text as its ASCII digit (a point
// stays one, the way ISO_DURATION writes fractional seconds), and reports
// whether text was nothing else.
func digitText(text string) (string, bool) {
	var builder strings.Builder
	for _, r := range text {
		if r == '.' {
			builder.WriteByte('.')
			continue
		}
		digit, ok := decimalDigit(r)
		if !ok {
			return "", false
		}
		builder.WriteByte(byte('0' + digit))
	}
	return builder.String(), true
}

// pythonInt is int(text) for a str in base 10: leading and trailing
// whitespace, an optional sign, then decimal digits (any script) with single
// underscores between them. More than 4300 digits raise in CPython.
func pythonInt(text string) (*big.Int, bool) {
	runes := []rune(text)
	at, end := 0, len(runes)
	for at < end && pythonSpace(runes[at]) {
		at++
	}
	for end > at && pythonSpace(runes[end-1]) {
		end--
	}
	negative := false
	if at < end && (runes[at] == '+' || runes[at] == '-') {
		negative = runes[at] == '-'
		at++
	}
	var digits strings.Builder
	count, previous := 0, false
	for ; at < end; at++ {
		if digit, ok := decimalDigit(runes[at]); ok {
			digits.WriteByte(byte('0' + digit))
			count, previous = count+1, true
			continue
		}
		if runes[at] == '_' && previous && at+1 < end {
			if _, ok := decimalDigit(runes[at+1]); ok {
				previous = false
				continue
			}
		}
		return nil, false
	}
	if count == 0 || count > 4300 {
		return nil, false
	}
	value, ok := new(big.Int).SetString(digits.String(), 10)
	if !ok {
		return nil, false
	}
	if negative {
		value.Neg(value)
	}
	return value, true
}

// pythonFloat is float(text) for a str: leading and trailing whitespace, an
// optional sign, then 'inf', 'infinity' or 'nan' in any case, or a decimal
// number with an optional exponent (single underscores may sit between
// digits). A value past float64's range is an infinity, as CPython's is.
func pythonFloat(text string) (float64, bool) {
	runes := []rune(text)
	at, end := 0, len(runes)
	for at < end && pythonSpace(runes[at]) {
		at++
	}
	for end > at && pythonSpace(runes[end-1]) {
		end--
	}
	negative := false
	if at < end && (runes[at] == '+' || runes[at] == '-') {
		negative = runes[at] == '-'
		at++
	}
	switch word := strings.ToLower(string(runes[at:end])); word {
	case "inf", "infinity":
		if negative {
			return math.Inf(-1), true
		}
		return math.Inf(1), true
	case "nan":
		return math.NaN(), true
	}
	// The decimal number in ASCII, for strconv: digits, an optional point and
	// an optional exponent.
	var ascii strings.Builder
	if negative {
		ascii.WriteByte('-')
	}
	digits, point, exponent := 0, false, false
	previous := false
	for at < end && !exponent {
		r := runes[at]
		if digit, ok := decimalDigit(r); ok {
			ascii.WriteByte(byte('0' + digit))
			digits, previous = digits+1, true
			at++
			continue
		}
		switch {
		case r == '_' && previous && at+1 < end:
			if _, ok := decimalDigit(runes[at+1]); !ok {
				return 0, false
			}
			previous = false
			at++
		case r == '.' && !point:
			point, previous = true, false
			ascii.WriteByte('.')
			at++
		case (r == 'e' || r == 'E') && digits > 0:
			exponent = true
			ascii.WriteByte('e')
			at++
			if at < end && (runes[at] == '+' || runes[at] == '-') {
				ascii.WriteByte(byte(runes[at]))
				at++
			}
		default:
			return 0, false
		}
	}
	if digits == 0 {
		return 0, false
	}
	if exponent && at == end {
		return 0, false // an exponent mark with no digits
	}
	for ; at < end; at++ {
		digit, ok := decimalDigit(runes[at])
		if !ok {
			return 0, false
		}
		ascii.WriteByte(byte('0' + digit))
	}
	value, err := strconv.ParseFloat(ascii.String(), 64)
	if err != nil {
		var number *strconv.NumError
		if !errors.As(err, &number) || number.Err != strconv.ErrRange {
			return 0, false
		}
	}
	return value, true
}

func isASCII(text string) bool {
	for index := 0; index < len(text); index++ {
		if text[index] >= 0x80 {
			return false
		}
	}
	return true
}

// floatRepr is repr() of a Python float (and str() of a numpy.float64).
func floatRepr(value float64) string {
	switch {
	case math.IsNaN(value):
		return "nan"
	case math.IsInf(value, 1):
		return "inf"
	case math.IsInf(value, -1):
		return "-inf"
	}
	scientific := strconv.FormatFloat(value, 'e', -1, 64)
	mantissa, exponentText, _ := strings.Cut(scientific, "e")
	exponent, _ := strconv.Atoi(exponentText)
	if exponent < -4 || exponent >= 16 {
		sign := "+"
		if exponent < 0 {
			sign, exponent = "-", -exponent
		}
		return mantissa + "e" + sign + leftPad(strconv.Itoa(exponent), 2)
	}
	fixed := strconv.FormatFloat(value, 'f', -1, 64)
	if !strings.Contains(fixed, ".") {
		fixed += ".0"
	}
	return fixed
}
