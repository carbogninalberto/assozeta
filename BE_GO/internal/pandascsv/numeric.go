package pandascsv

import (
	"math"
	"runtime"
	"strconv"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// isSpace and isDigit are the tokenizer's isspace_ascii and isdigit_ascii.
func isSpace(c byte) bool { return c == ' ' || c >= '\t' && c <= '\r' }
func isDigit(c byte) bool { return c >= '0' && c <= '9' }

// at is word[i] of a C string: NUL past the end.
func at(word []byte, i int) byte {
	if i < len(word) {
		return word[i]
	}
	return 0
}

const (
	numberOK = iota
	numberInvalid
	numberOverflow
)

// parseInt64 is str_to_int64 without a thousands separator.
func parseInt64(word []byte) (int64, int) {
	p := 0
	for isSpace(at(word, p)) {
		p++
	}
	negative := at(word, p) == '-'
	if negative || at(word, p) == '+' {
		p++
	}
	if !isDigit(at(word, p)) {
		return 0, numberInvalid
	}
	var number int64
	for ; isDigit(at(word, p)); p++ {
		digit := int64(word[p] - '0')
		if negative {
			if number > math.MinInt64/10 || number == math.MinInt64/10 && digit <= 8 {
				number = number*10 - digit
			} else {
				return 0, numberOverflow
			}
		} else if number < math.MaxInt64/10 || number == math.MaxInt64/10 && digit <= 7 {
			number = number*10 + digit
		} else {
			return 0, numberOverflow
		}
	}
	for isSpace(at(word, p)) {
		p++
	}
	if p < len(word) {
		return 0, numberInvalid
	}
	return number, numberOK
}

// uintState is uint_state.
type uintState struct{ signed, unsigned, null bool }

// parseUint64 is str_to_uint64: a leading '-' only marks the column signed.
func parseUint64(state *uintState, word []byte) (uint64, int) {
	p := 0
	for isSpace(at(word, p)) {
		p++
	}
	if at(word, p) == '-' {
		state.signed = true
		return 0, numberOK
	}
	if at(word, p) == '+' {
		p++
	}
	if !isDigit(at(word, p)) {
		return 0, numberInvalid
	}
	var number uint64
	for ; isDigit(at(word, p)); p++ {
		digit := uint64(word[p] - '0')
		if number < math.MaxUint64/10 || number == math.MaxUint64/10 && digit <= 5 {
			number = number*10 + digit
		} else {
			return 0, numberOverflow
		}
	}
	for isSpace(at(word, p)) {
		p++
	}
	if p < len(word) {
		return 0, numberInvalid
	}
	if number > math.MaxInt64 {
		state.unsigned = true
	}
	return number, numberOK
}

var powersOfTen = func() (table [309]float64) {
	for exponent := range table {
		// The correctly rounded value of the literal 1e<exponent>.
		table[exponent], _ = strconv.ParseFloat("1e"+strconv.Itoa(exponent), 64)
	}
	return table
}()

const (
	floatOK = iota
	floatInvalid
	// floatPlatform is text whose value depends on how pandas was compiled,
	// on a platform the measurement in floatFMA does not cover.
	floatPlatform
)

// errFloatPlatform is an application state: the value of a float text here
// depends on whether the pandas wheel contracted the digit accumulation, and
// this platform's wheel was never measured (images are built for Linux only).
var errFloatPlatform = refusal.AppState("pandascsv: float value depends on the pandas build")

// floatFMA is how the pandas wheel of this platform accumulates the digits of a
// float: 'number * 10 + digit' is contracted into a fused multiply-add by the
// macOS arm64 wheel and not by the Linux wheels. Measured with pandas 2.2.3 on
// '3.14159265358979323846': 0x400921fb54442d18 on darwin/arm64,
// 0x400921fb54442d17 on linux/amd64 and linux/arm64; beyond 2**53 the two
// differ. Same measurement and rule as internal/xlsxexport/pandas.go, which
// converts the same texts with the same pandas function.
var floatFMA = map[string]bool{
	"darwin/arm64": true,
	"linux/amd64":  false,
	"linux/arm64":  false,
}

// parseFloat is precise_xstrtod (float_precision 'high', the C parser's
// default) followed by _try_double's checks: the whole word must be consumed.
// It is not a correctly rounded conversion: up to 17 digits are accumulated
// in a double and scaled by one power of ten.
//
// The fused and the plain accumulation differ beyond 2**53 (floatFMA); the
// value of this platform's wheel is returned, and where they differ on an
// unmeasured platform the status is floatPlatform. The exponent is the C int
// it is accumulated in: an int32, wrapping like two's complement, both in its
// digits and in its sum (pandas 2.2.3: '1e4294967296' is 1.0, '1e2147483648'
// is 0.0, '1e-2147483649' is not a float).
func parseFloat(word []byte) (float64, int) {
	fused, plain, status := xstrtod(word)
	if status == floatInvalid {
		return 0, floatInvalid
	}
	if fused == plain {
		return fused, floatOK
	}
	contracted, measured := floatFMA[runtime.GOOS+"/"+runtime.GOARCH]
	if !measured {
		return 0, floatPlatform
	}
	if contracted {
		return fused, floatOK
	}
	return plain, floatOK
}

// xstrtod is precise_xstrtod with the two digit accumulations the wheels
// compile: fused contracts 'number * 10 + digit', plain does two rounded
// operations (the explicit conversions keep Go from contracting it too).
func xstrtod(word []byte) (fused, plain float64, status int) {
	p := 0
	for isSpace(at(word, p)) {
		p++
	}
	negative := false
	switch at(word, p) {
	case '-':
		negative = true
		p++
	case '+':
		p++
	}
	const maxDigits = 17
	fused, plain = 0.0, 0.0
	accumulate := func(digit byte) {
		fused = math.FMA(fused, 10, float64(digit-'0'))
		plain = float64(plain*10) + float64(digit-'0')
	}
	exponent, digits, decimals := int32(0), 0, int32(0)
	for ; isDigit(at(word, p)); p++ {
		if digits < maxDigits {
			accumulate(word[p])
			digits++
		} else {
			exponent++
		}
	}
	if at(word, p) == '.' {
		p++
		for digits < maxDigits && isDigit(at(word, p)) {
			accumulate(word[p])
			digits++
			decimals++
			p++
		}
		if digits >= maxDigits {
			for isDigit(at(word, p)) {
				p++
			}
		}
		exponent -= decimals
	}
	if digits == 0 {
		return 0, 0, floatInvalid
	}
	if negative {
		fused, plain = -fused, -plain
	}
	if c := at(word, p); c == 'e' || c == 'E' {
		p++
		negative = false
		switch at(word, p) {
		case '-':
			negative = true
			p++
		case '+':
			p++
		}
		count, n := 0, int32(0)
		for ; count < maxDigits && isDigit(at(word, p)); p++ {
			n = n*10 + int32(word[p]-'0')
			count++
		}
		if negative {
			exponent -= n
		} else {
			exponent += n
		}
		if count == 0 {
			p--
		}
	}
	switch {
	case exponent > 308:
		return 0, 0, floatInvalid
	case exponent > 0:
		power := powersOfTen[exponent]
		fused, plain = fused*power, plain*power
	case exponent < -308:
		if exponent < -616 {
			fused, plain = 0, 0
		} else {
			first, second := powersOfTen[-308-exponent], powersOfTen[308]
			fused, plain = fused/first/second, plain/first/second
		}
	default:
		power := powersOfTen[-exponent]
		fused, plain = fused/power, plain/power
	}
	if math.IsInf(fused, 0) || math.IsInf(plain, 0) {
		return 0, 0, floatInvalid
	}
	for isSpace(at(word, p)) {
		p++
	}
	if p < len(word) {
		return 0, 0, floatInvalid
	}
	return fused, plain, floatOK
}
