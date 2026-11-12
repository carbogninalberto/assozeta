package pandascsv

import (
	"math"
	"strconv"
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
	// floatPlatform is text whose value depends on how pandas was compiled.
	floatPlatform
)

// parseFloat is precise_xstrtod (float_precision 'high', the C parser's
// default) followed by _try_double's checks: the whole word must be consumed.
// It is not a correctly rounded conversion: up to 17 digits are accumulated
// in a double and scaled by one power of ten.
//
// 'number * 10 + digit' is one fused multiply-add where the compiler
// contracts it (the arm64 wheels) and two rounded operations elsewhere (the
// x86-64 wheels); text for which the two disagree is floatPlatform. So is an
// exponent that overflows the C int it is accumulated in.
func parseFloat(word []byte) (float64, int) {
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
	number, fused := 0.0, 0.0
	exponent, digits, decimals := 0, 0, 0
	push := func(c byte) {
		digit := float64(c - '0')
		number = float64(number*10) + digit
		fused = math.FMA(fused, 10, digit)
		digits++
	}
	for ; isDigit(at(word, p)); p++ {
		if digits < maxDigits {
			push(word[p])
		} else {
			exponent++
		}
	}
	if at(word, p) == '.' {
		p++
		for ; digits < maxDigits && isDigit(at(word, p)); p++ {
			push(word[p])
			decimals++
		}
		if digits >= maxDigits {
			for isDigit(at(word, p)) {
				p++
			}
		}
		exponent -= decimals
	}
	if digits == 0 {
		return 0, floatInvalid
	}
	if number != fused {
		return 0, floatPlatform
	}
	if exponent > math.MaxInt32 {
		return 0, floatPlatform
	}
	if negative {
		number = -number
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
		count, n := 0, 0
		for ; count < maxDigits && isDigit(at(word, p)); p++ {
			n = n*10 + int(word[p]-'0')
			if n > math.MaxInt32 {
				return 0, floatPlatform
			}
			count++
		}
		if negative {
			exponent -= n
		} else {
			exponent += n
		}
		if exponent > math.MaxInt32 || exponent < math.MinInt32 {
			return 0, floatPlatform
		}
		if count == 0 {
			p--
		}
	}
	switch {
	case exponent > 308:
		return 0, floatInvalid
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
		return 0, floatInvalid
	}
	for isSpace(at(word, p)) {
		p++
	}
	if p < len(word) {
		return 0, floatInvalid
	}
	return number, floatOK
}
