package pydateutil

import (
	"math/big"
	"strings"
)

// maxStrDigits is sys.get_int_max_str_digits(), CPython's default.
const maxStrDigits = 4300

var (
	bigTen   = big.NewInt(10)
	bigSixty = big.NewInt(60)
	// decimalPrec is the precision of decimal's default context.
	decimalPrec = 28
	pow28       = new(big.Int).Exp(bigTen, big.NewInt(28), nil)
)

func small(v int64) *big.Int { return big.NewInt(v) }

// pyInt is int(s) for the strings the parser passes: decimal digits of any
// script, at most maxStrDigits of them. ok is false where int() raises
// ValueError.
func pyInt(s string) (*big.Int, bool) {
	digits := make([]byte, 0, len(s))
	for _, r := range s {
		d := decimalValue(r)
		if d < 0 || len(digits) == maxStrDigits {
			return nil, false
		}
		digits = append(digits, byte('0'+d))
	}
	if len(digits) == 0 {
		return nil, false
	}
	v, ok := new(big.Int).SetString(string(digits), 10)
	return v, ok
}

// decimal is a finite, non-negative decimal.Decimal as written: an integer
// part and the fraction digits.
type decimal struct {
	ip   *big.Int
	frac string
}

// parseDecimal is Decimal(token) for a token of the tokenizer: digits with
// at most one '.', in any decimal script. ok is false where Decimal raises
// or the value is not finite.
func parseDecimal(token string) (decimal, bool) {
	var ip, frac []byte
	dot := false
	for _, r := range token {
		switch d := decimalValue(r); {
		case d >= 0 && dot:
			frac = append(frac, byte('0'+d))
		case d >= 0:
			ip = append(ip, byte('0'+d))
		case r == '.' && !dot:
			dot = true
		default:
			return decimal{}, false
		}
	}
	if len(ip)+len(frac) == 0 {
		return decimal{}, false
	}
	value := new(big.Int)
	if len(ip) > 0 {
		value.SetString(string(ip), 10)
	}
	return decimal{ip: value, frac: string(frac)}, true
}

// isFloat reports whether float(token) succeeds: the finite decimals above
// and the names float() accepts, which Decimal() then finds non-finite.
func isFloat(token string) bool {
	if _, ok := parseDecimal(token); ok {
		return true
	}
	switch word, _ := lowerASCII(token); word {
	case "inf", "infinity", "nan":
		return true
	}
	return false
}

func (d decimal) fracNonZero() bool { return strings.Trim(d.frac, "0") != "" }

// lessEqual is `d <= n`.
func (d decimal) lessEqual(n int64) bool {
	c := d.ip.Cmp(small(n))
	return c < 0 || (c == 0 && !d.fracNonZero())
}

// greater is `d > n`.
func (d decimal) greater(n int64) bool { return !d.lessEqual(n) }

// rem1 is bool(d % 1) in the default context: the remainder cannot be
// computed when the integer quotient needs more than 28 digits.
func (d decimal) rem1() (bool, *Error) {
	if d.ip.Cmp(pow28) >= 0 {
		return false, &Error{Class: ClassInvalidOperation, Message: "[<class 'decimal.DivisionImpossible'>]"}
	}
	return d.fracNonZero(), nil
}

// roundPrec rounds coefficient*10^exp to the context precision, half even.
func roundPrec(coefficient *big.Int, exp int) (*big.Int, int) {
	digits := len(coefficient.String())
	if digits <= decimalPrec {
		return coefficient, exp
	}
	drop := digits - decimalPrec
	scale := new(big.Int).Exp(bigTen, small(int64(drop)), nil)
	q, r := new(big.Int).QuoRem(coefficient, scale, new(big.Int))
	half := new(big.Int).Rsh(scale, 1)
	if c := r.Cmp(half); c > 0 || (c == 0 && q.Bit(0) == 1) {
		q.Add(q, small(1))
	}
	return q, exp + drop
}

// sixtyFrac is int(60 * (d % 1)): the remainder and the product are each
// rounded to 28 significant digits before the truncation, so a fraction
// close enough to one yields 60.
func (d decimal) sixtyFrac() *big.Int {
	significant := strings.TrimLeft(d.frac, "0")
	if significant == "" {
		return small(0)
	}
	coefficient, _ := new(big.Int).SetString(significant, 10)
	coefficient, exp := roundPrec(coefficient, -len(d.frac))
	coefficient, exp = roundPrec(new(big.Int).Mul(coefficient, bigSixty), exp)
	if exp >= 0 {
		return coefficient.Mul(coefficient, new(big.Int).Exp(bigTen, small(int64(exp)), nil))
	}
	return coefficient.Quo(coefficient, new(big.Int).Exp(bigTen, small(int64(-exp)), nil))
}

// lowerASCII is str.lower() for the keyword lookups. ok is false for a token
// with a non-ASCII character: its lowercase form is never a keyword (the only
// non-ASCII character lowering to ASCII is KELVIN SIGN, to 'k', which no
// keyword contains; gen_corpus.py checks both facts).
func lowerASCII(token string) (string, bool) {
	for i := 0; i < len(token); i++ {
		if token[i] >= 0x80 {
			return "", false
		}
	}
	return strings.ToLower(token), true
}

// isLeap is calendar.isleap for any integer year.
func isLeap(year *big.Int) bool {
	y := new(big.Int).Mod(year, small(400)).Int64()
	return y%4 == 0 && (y%100 != 0 || y%400 == 0)
}

var monthDays = [13]int64{0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31}

// daysInMonth is calendar.monthrange(year, month)[1] for month in 1..12.
func daysInMonth(year *big.Int, month int64) int64 {
	if month == 2 && isLeap(year) {
		return 29
	}
	return monthDays[month]
}
