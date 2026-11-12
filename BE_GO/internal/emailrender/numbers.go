package emailrender

import (
	"math"
	"math/big"
	"regexp"
	"strconv"
	"strings"
)

// decimalValue is a finite decimal.Decimal: sign, coefficient and exponent
// (as_tuple), with the coefficient's digit count as Decimal keeps it.
type decimalValue struct {
	negative    bool
	coefficient *big.Int
	digits      int
	exponent    int
}

var decimalPattern = regexp.MustCompile(`^([-+]?)([0-9]*)(?:\.([0-9]*))?(?:[eE]([-+]?[0-9]+))?$`)

// parseDecimal accepts the finite forms str() of a Decimal, an int or a
// float produces; anything else (spaces, underscores, NaN, Infinity) is
// refused rather than reproducing decimal's full grammar.
func parseDecimal(text string) (decimalValue, error) {
	match := decimalPattern.FindStringSubmatch(text)
	if match == nil || match[2]+match[3] == "" || len(text) > 64 {
		return decimalValue{}, unsupported("decimal %q", text)
	}
	exponent := 0
	if match[4] != "" {
		value, err := strconv.Atoi(match[4])
		if err != nil || value > 1000 || value < -1000 {
			return decimalValue{}, unsupported("decimal %q", text)
		}
		exponent = value
	}
	digits := strings.TrimLeft(match[2]+match[3], "0")
	if digits == "" {
		digits = "0"
	}
	coefficient, _ := new(big.Int).SetString(digits, 10)
	return decimalValue{negative: match[1] == "-", coefficient: coefficient, digits: len(digits),
		exponent: exponent - len(match[3])}, nil
}

func pow10(n int) *big.Int { return new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(n)), nil) }

// pythonFloatRepr is repr(float) (shortest round-trip digits; exponent
// notation below 1e-4 and from 1e16). Non-finite values are refused.
func pythonFloatRepr(value float64) (string, error) {
	if math.IsInf(value, 0) || math.IsNaN(value) {
		return "", unsupported("non-finite float")
	}
	mantissa, exponent := floatDigits(value)
	sign := ""
	if math.Signbit(value) {
		sign = "-"
	}
	if exponent < -4 || exponent >= 16 {
		text := mantissa[:1]
		if len(mantissa) > 1 {
			text += "." + mantissa[1:]
		}
		e := "+"
		if exponent < 0 {
			e, exponent = "-", -exponent
		}
		return sign + text + "e" + e + leftPad(strconv.Itoa(exponent), 2), nil
	}
	return sign + fixedDigits(mantissa, exponent, true), nil
}

// floatDigits returns the shortest digits of |value| and the decimal
// exponent of the first digit.
func floatDigits(value float64) (string, int) {
	formatted := strconv.FormatFloat(math.Abs(value), 'e', -1, 64)
	mantissa, exponentText, _ := strings.Cut(formatted, "e")
	exponent, _ := strconv.Atoi(exponentText)
	return strings.Replace(mantissa, ".", "", 1), exponent
}

// fixedDigits places the decimal point in digits whose first digit has the
// given decimal exponent; pointZero appends ".0" to integers (repr).
func fixedDigits(digits string, exponent int, pointZero bool) string {
	if exponent < 0 {
		return "0." + strings.Repeat("0", -exponent-1) + digits
	}
	if len(digits) <= exponent+1 {
		whole := digits + strings.Repeat("0", exponent+1-len(digits))
		if pointZero {
			return whole + ".0"
		}
		return whole
	}
	return digits[:exponent+1] + "." + digits[exponent+1:]
}

func leftPad(text string, width int) string {
	if len(text) >= width {
		return text
	}
	return strings.Repeat("0", width-len(text)) + text
}

// numberFormat is django.utils.numberformat.format for a str number with
// grouping off: the sign, the integer part and decimal_pos decimals (cut
// or zero-padded) after the separator.
func numberFormat(number string, decimalPos int, hasPos bool, separator string) string {
	sign := ""
	if strings.HasPrefix(number, "-") {
		sign, number = "-", number[1:]
	}
	integer, fraction, _ := strings.Cut(number, ".")
	if hasPos {
		if len(fraction) > decimalPos {
			fraction = fraction[:decimalPos]
		}
		fraction += strings.Repeat("0", decimalPos-len(fraction))
	}
	if fraction != "" {
		fraction = separator + fraction
	}
	return sign + integer + fraction
}

// localizeDecimal is formats.localize(Decimal): number_format with
// decimal_pos None ("{:f}" of the value, scientific notation beyond 200
// digits as numberformat.format avoids the large fixed text).
func localizeDecimal(text, separator string) (string, error) {
	value, err := parseDecimal(text)
	if err != nil {
		return "", err
	}
	if value.digits+abs(value.exponent) > 200 {
		return numberFormat(value.scientific(), 0, false, separator), nil
	}
	return numberFormat(value.fixed(), 0, false, separator), nil
}

// localizeFloat is formats.localize(float): str(float), through Decimal
// when it has an exponent; inf and nan stay as str(float) writes them.
func localizeFloat(number float64, separator string) (string, error) {
	switch {
	case math.IsNaN(number):
		return "nan", nil
	case math.IsInf(number, 1):
		return "inf", nil
	case math.IsInf(number, -1):
		return "-inf", nil
	}
	text, err := pythonFloatRepr(number)
	if err != nil {
		return "", err
	}
	if strings.Contains(text, "e") {
		return localizeDecimal(text, separator)
	}
	return numberFormat(text, 0, false, separator), nil
}

// fixed is "{:f}".format(Decimal): plain notation keeping the exponent's
// decimals (Decimal('1.50') -> '1.50', Decimal('1E+2') -> '100').
func (d decimalValue) fixed() string {
	digits := d.coefficient.String()
	sign := ""
	if d.negative {
		sign = "-"
	}
	if d.exponent >= 0 {
		if d.coefficient.Sign() == 0 {
			return sign + "0"
		}
		return sign + digits + strings.Repeat("0", d.exponent)
	}
	places := -d.exponent
	if len(digits) <= places {
		digits = strings.Repeat("0", places-len(digits)+1) + digits
	}
	return sign + digits[:len(digits)-places] + "." + digits[len(digits)-places:]
}

// scientific is "{:e}".format(Decimal): every coefficient digit, then the
// exponent of the first digit with its sign (Decimal('1.500E+250') ->
// '1.500e+250').
func (d decimalValue) scientific() string {
	digits := d.coefficient.String()
	sign := ""
	if d.negative {
		sign = "-"
	}
	coefficient := digits[:1]
	if len(digits) > 1 {
		coefficient += "." + digits[1:]
	}
	exponent := d.exponent + d.digits - 1
	mark := "+"
	if exponent < 0 {
		mark, exponent = "-", -exponent
	}
	return sign + coefficient + "e" + mark + strconv.Itoa(exponent)
}

func abs(n int) int {
	if n < 0 {
		return -n
	}
	return n
}

// floatformat ports django.template.defaultfilters.floatformat with an
// integer argument (the "g"/"u" suffixes are refused) for None, int, float
// and Decimal input; the result is safe.
func floatformat(value any, arg any, separator string) (any, error) {
	precision, ok := arg.(int64)
	if arg == nil {
		precision, ok = -1, true
	}
	if !ok || precision > 1000 || precision < -1000 {
		return nil, unsupported("floatformat argument %v", arg)
	}
	var input string
	switch v := value.(type) {
	case nil:
		// Decimal('None') and float(None) both fail.
		return "", nil
	case string:
		if v == "" {
			return "", nil
		}
		return nil, unsupported("floatformat of text")
	case int64:
		input = strconv.FormatInt(v, 10)
	case float64:
		text, err := pythonFloatRepr(v)
		if err != nil {
			return nil, err
		}
		input = text
	case Decimal:
		input = string(v)
	default:
		return nil, unsupported("floatformat of %T", value)
	}
	d, err := parseDecimal(input)
	if err != nil {
		return nil, err
	}
	if d.digits+abs(d.exponent) > 200 {
		return nil, unsupported("floatformat beyond 200 digits")
	}
	// m = int(d) - d: whether a fractional part exists.
	fractional := false
	whole := new(big.Int).Set(d.coefficient)
	if d.exponent < 0 {
		var remainder big.Int
		whole.QuoRem(whole, pow10(-d.exponent), &remainder)
		fractional = remainder.Sign() != 0
	} else {
		whole.Mul(whole, pow10(d.exponent))
	}
	if !fractional && precision <= 0 {
		number := whole.String()
		if d.negative && whole.Sign() != 0 {
			number = "-" + number
		}
		return Safe(numberFormat(number, 0, true, separator)), nil
	}
	places := abs(int(precision))
	// quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP)
	scaled := new(big.Int).Set(d.coefficient)
	if shift := places + d.exponent; shift >= 0 {
		scaled.Mul(scaled, pow10(shift))
	} else {
		divisor := pow10(-shift)
		var remainder big.Int
		scaled.QuoRem(scaled, divisor, &remainder)
		if new(big.Int).Mul(&remainder, big.NewInt(2)).Cmp(divisor) >= 0 {
			scaled.Add(scaled, big.NewInt(1))
		}
	}
	digits := scaled.String()
	if len(digits) <= places {
		digits = strings.Repeat("0", places-len(digits)+1) + digits
	}
	number := digits[:len(digits)-places] + "." + digits[len(digits)-places:]
	if d.negative && scaled.Sign() != 0 {
		number = "-" + number
	}
	return Safe(numberFormat(number, places, true, separator)), nil
}
