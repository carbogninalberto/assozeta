package pandasxlsx

import (
	"math"
	"math/big"
	"strconv"
	"strings"
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
	// uncertain: f depends on how the tokenizer was compiled.
	uncertain bool
}

// preciseStrtod is the tokenizer's precise_xstrtod (decimal '.', exponent
// 'e', no thousands separator, trailing whitespace skipped) followed by
// to_double's end-of-text check. certain is false where the C result depends
// on the build: a 17th digit accumulated with or without a fused
// multiply-add, or an exponent of more than nine digits.
func preciseStrtod(text string) (value float64, maybeInt, ok, certain bool) {
	at := func(index int) byte {
		if index < len(text) {
			return text[index]
		}
		return 0
	}
	const maxDigits = 17
	p := 0
	maybeInt, certain = true, true
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
	number, fused := 0.0, 0.0
	exponent, digits, decimals := 0, 0, 0
	push := func(c byte) {
		digit := float64(c - '0')
		// The conversion keeps the product and the sum two rounded operations.
		number = float64(number*10) + digit
		fused = math.FMA(fused, 10, digit)
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
		return 0, false, false, true
	}
	if number != fused {
		certain = false
	}
	if negative {
		number = -number
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
		count, n := 0, 0
		for count < maxDigits && isASCIIDigit(at(p)) {
			if count < 9 {
				n = n*10 + int(at(p)-'0')
			} else {
				certain = false
			}
			count++
			p++
		}
		if count == 0 {
			return 0, false, false, true // the exponent mark is not consumed
		}
		if negative {
			exponent -= n
		} else {
			exponent += n
		}
	}
	switch {
	case exponent > 308:
		return 0, false, false, certain
	case exponent > 0:
		number *= powersOfTen[exponent]
	case exponent < -308:
		if exponent < -616 {
			number = 0
		} else {
			number /= powersOfTen[-308-exponent]
			number /= powersOfTen[308]
		}
	default:
		number /= powersOfTen[-exponent]
	}
	if math.IsInf(number, 0) {
		return 0, false, false, certain
	}
	for isASCIISpace(at(p)) {
		p++
	}
	if p != len(text) {
		return 0, false, false, true
	}
	return number, maybeInt, true, certain
}

// numericText is floatify and the int() that follows it in
// maybe_convert_numeric, for a str that is not an NA value. ok is false where
// the conversion raises (text that is no number, an integer outside int64 and
// uint64). ErrUnsupported: whether it raises depends on the build.
func numericText(text string) (numeric, bool, error) {
	if strings.IndexByte(text, 0) >= 0 {
		return numeric{}, false, ErrUnsupported
	}
	value, maybeInt, ok, certain := preciseStrtod(text)
	if !ok {
		if !certain {
			return numeric{}, false, ErrUnsupported
		}
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
		return numeric{f: value, uncertain: !certain}, true, nil
	}
	body := strings.TrimFunc(text, func(r rune) bool { return r < 0x80 && isASCIISpace(byte(r)) })
	if len(body) > 4300 {
		return numeric{}, false, nil // int() refuses more than 4300 digits
	}
	integer, parsed := new(big.Int).SetString(strings.TrimPrefix(body, "+"), 10)
	if !parsed {
		return numeric{}, false, ErrUnsupported
	}
	if integer.Cmp(minInt64) < 0 || integer.Cmp(maxUint64) > 0 {
		return numeric{}, false, nil // 'Integer out of range.'
	}
	return numeric{integer: true, i: integer, f: value, uncertain: !certain}, true, nil
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
