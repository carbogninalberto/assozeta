package fatturapa

import (
	"math"
	"math/big"
	"regexp"
	"strconv"
	"strings"
)

// precision is Python's default decimal context precision (prec=28).
const precision = 28

// decimal is a Python decimal.Decimal finite value: (-1)^neg * coef * 10^exp,
// trailing zeros and the sign of zero kept.
type decimal struct {
	neg  bool
	coef *big.Int // >= 0
	exp  int
}

var (
	bigTen      = big.NewInt(10)
	plainNumber = regexp.MustCompile(`^([+-]?)([0-9]*)(?:\.([0-9]*))?(?:[eE]([+-]?[0-9]+))?$`)
)

func pow10(n int) *big.Int { return new(big.Int).Exp(bigTen, big.NewInt(int64(n)), nil) }

func decimalOfInt(value *big.Int) decimal {
	return decimal{neg: value.Sign() < 0, coef: new(big.Int).Abs(value)}
}

// decimalOfFloat is Decimal.from_float: the exact binary value.
func decimalOfFloat(value float64) decimal {
	neg := math.Signbit(value)
	value = math.Abs(value)
	if value == 0 {
		return decimal{neg: neg, coef: new(big.Int)}
	}
	mantissa, exponent := math.Frexp(value)
	m := new(big.Int).SetUint64(uint64(math.Ldexp(mantissa, 53)))
	e := exponent - 53
	for e < 0 && m.Bit(0) == 0 {
		m.Rsh(m, 1)
		e++
	}
	if e >= 0 {
		return decimal{neg: neg, coef: m.Lsh(m, uint(e))}
	}
	five := new(big.Int).Exp(big.NewInt(5), big.NewInt(int64(-e)), nil)
	return decimal{neg: neg, coef: m.Mul(m, five), exp: e}
}

// parseDecimal is Decimal(str) for ASCII text without whitespace or
// underscores: ok false when Python raises (InvalidOperation, or a special
// value the field's quantize step rejects).
func parseDecimal(text string) (decimal, bool, error) {
	for _, r := range text {
		if r > 0x7f || r == '_' || strings.ContainsRune(" \t\n\r\v\f\x1c\x1d\x1e\x1f", r) {
			// Python strips Unicode whitespace, accepts underscores and Unicode digits.
			return decimal{}, false, ErrUnsupported
		}
	}
	match := plainNumber.FindStringSubmatch(text)
	if match == nil || match[2]+match[3] == "" {
		return decimal{}, false, nil
	}
	exp := 0
	if match[4] != "" {
		value, err := strconv.Atoi(match[4])
		if err != nil || value > 100000 || value < -100000 {
			return decimal{}, false, ErrUnsupported
		}
		exp = value
	}
	coef, _ := new(big.Int).SetString(match[2]+match[3], 10)
	return decimal{neg: match[1] == "-", coef: coef, exp: exp - len(match[3])}, true, nil
}

func (d decimal) digits() int {
	if d.coef.Sign() == 0 {
		return 1
	}
	return len(d.coef.String())
}

func (d decimal) isZero() bool { return d.coef.Sign() == 0 }

func (d decimal) signed() *big.Int {
	value := new(big.Int).Set(d.coef)
	if d.neg {
		value.Neg(value)
	}
	return value
}

// rescale rounds to exponent exp (half up or half even); exact when exp <= d.exp.
func (d decimal) rescale(exp int, halfEven bool) decimal {
	if exp <= d.exp {
		return decimal{neg: d.neg, coef: new(big.Int).Mul(d.coef, pow10(d.exp-exp)), exp: exp}
	}
	divisor := pow10(exp - d.exp)
	quotient, remainder := new(big.Int).QuoRem(d.coef, divisor, new(big.Int))
	twice := remainder.Lsh(remainder, 1)
	switch cmp := twice.Cmp(divisor); {
	case cmp > 0, cmp == 0 && (!halfEven || quotient.Bit(0) == 1):
		quotient.Add(quotient, big.NewInt(1))
	}
	return decimal{neg: d.neg, coef: quotient, exp: exp}
}

// quantize is Decimal.quantize(10**exp, ROUND_HALF_UP) in the default
// context: a result longer than the precision raises InvalidOperation.
func (d decimal) quantize(exp int) (decimal, bool) {
	result := d.rescale(exp, false)
	return result, result.digits() <= precision
}

// clean is DecimalField.clean_value's quantize step for decimals (min, max).
func (d decimal) clean(minimum, maximum int) (decimal, bool) {
	switch {
	case d.exp < 0 && -d.exp < minimum:
		return d.quantize(-minimum)
	case d.exp < 0 && -d.exp > maximum:
		return d.quantize(-maximum)
	case d.exp >= 0 && minimum > 0:
		return d.quantize(-minimum)
	}
	return d, true
}

// add is context addition; exact false when Python would round (more than 28 digits).
func (d decimal) add(other decimal) (decimal, bool) {
	exp := min(d.exp, other.exp)
	sum := new(big.Int).Add(d.rescale(exp, false).signed(), other.rescale(exp, false).signed())
	result := decimal{neg: sum.Sign() < 0, coef: sum.Abs(sum), exp: exp}
	if result.isZero() {
		result.neg = d.neg && other.neg
	}
	return result, result.digits() <= precision
}

func (d decimal) mul(other decimal) (decimal, bool) {
	result := decimal{neg: d.neg != other.neg, coef: new(big.Int).Mul(d.coef, other.coef), exp: d.exp + other.exp}
	return result, result.digits() <= precision
}

// div100 is d / 100 (always exact): the quotient at the ideal exponent d.exp
// when it fits, otherwise the smallest exponent that represents it.
func (d decimal) div100() decimal {
	result := decimal{neg: d.neg, coef: new(big.Int).Set(d.coef), exp: d.exp - 2}
	if result.isZero() {
		result.exp = d.exp
		return result
	}
	remainder := new(big.Int)
	for result.exp < d.exp {
		quotient, rem := new(big.Int).QuoRem(result.coef, bigTen, remainder)
		if rem.Sign() != 0 {
			break
		}
		result.coef = quotient
		result.exp++
	}
	return result
}

func (d decimal) cmp(other decimal) int {
	exp := min(d.exp, other.exp)
	return d.rescale(exp, false).signed().Cmp(other.rescale(exp, false).signed())
}

// String is str(Decimal): the to-scientific-string conversion.
func (d decimal) String() string {
	digits := d.coef.String()
	sign := ""
	if d.neg {
		sign = "-"
	}
	adjusted := d.exp + len(digits) - 1
	if d.exp <= 0 && adjusted >= -6 {
		if d.exp == 0 {
			return sign + digits
		}
		point := len(digits) + d.exp
		if point > 0 {
			return sign + digits[:point] + "." + digits[point:]
		}
		return sign + "0." + strings.Repeat("0", -point) + digits
	}
	text := digits[:1]
	if len(digits) > 1 {
		text += "." + digits[1:]
	}
	mark := "+"
	if adjusted < 0 {
		mark = ""
	}
	return sign + text + "E" + mark + strconv.Itoa(adjusted)
}

// fixed2 is format(d, '.2f'): rounded half even to two places.
func (d decimal) fixed2() string {
	r := d.rescale(-2, true)
	digits := r.coef.String()
	if len(digits) < 3 {
		digits = strings.Repeat("0", 3-len(digits)) + digits
	}
	sign := ""
	if r.neg {
		sign = "-"
	}
	return sign + digits[:len(digits)-2] + "." + digits[len(digits)-2:]
}
