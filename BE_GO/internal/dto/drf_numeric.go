package dto

import (
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"unicode"
)

// Validation codes rendered by the payment-category message table.
const (
	DRFValidationInteger         = "integer"
	DRFValidationNumber          = "number"
	DRFValidationMaxStringLength = "max-string-length"
)

var drfTrailingDecimal = regexp.MustCompile(`\.0*\s*$`)

// DRFInteger is IntegerField.to_internal_value: int() of str(data) after
// removing a trailing ".000"; strings longer than 1000 characters fail first.
func DRFInteger(value any) (*big.Int, string) {
	if text, ok := value.(string); ok && len([]rune(text)) > 1000 {
		return nil, DRFValidationMaxStringLength
	}
	text := drfTrailingDecimal.ReplaceAllString(PythonStr(value), "")
	text = strings.TrimFunc(text, unicode.IsSpace)
	if !pythonIntLiteral.MatchString(text) {
		return nil, DRFValidationInteger
	}
	number, ok := new(big.Int).SetString(strings.ReplaceAll(text, "_", ""), 10)
	if !ok {
		return nil, DRFValidationInteger
	}
	return number, ""
}

// pythonDecimalLiteral is decimal.Decimal()'s finite grammar (digits may be
// grouped by single underscores).
var pythonDecimalLiteral = regexp.MustCompile(`^([+-])?((?:\d(?:_?\d)*)?)(?:\.((?:\d(?:_?\d)*)?))?(?:[eE]([+-]?\d(?:_?\d)*))?$`)

// DRFDecimal is DecimalField.to_internal_value plus validate_precision and
// quantize: it returns the quantized decimal text or an error code.
func DRFDecimal(value any, maxDigits, places int) (string, string) {
	text := strings.TrimFunc(PythonStr(value), unicode.IsSpace)
	if len([]rune(text)) > 1000 {
		return "", DRFValidationMaxStringLength
	}
	match := pythonDecimalLiteral.FindStringSubmatch(text)
	if match == nil || match[2] == "" && match[3] == "" {
		return "", DRFValidationNumber
	}
	sign := match[1]
	whole, fraction := strings.ReplaceAll(match[2], "_", ""), strings.ReplaceAll(match[3], "_", "")
	exponent := 0
	if match[4] != "" {
		parsed, err := strconv.Atoi(strings.ReplaceAll(match[4], "_", ""))
		if err != nil {
			return "", DRFValidationNumber
		}
		exponent = parsed
	}
	exponent -= len(fraction)
	coefficient := strings.TrimLeft(whole+fraction, "0")
	if coefficient == "" {
		coefficient = "0"
	}
	digits := len(coefficient)
	var total, wholeDigits, decimals int
	switch {
	case exponent >= 0:
		total, wholeDigits, decimals = digits+exponent, digits+exponent, 0
	case digits > -exponent:
		total, wholeDigits, decimals = digits, digits+exponent, -exponent
	default:
		total, wholeDigits, decimals = -exponent, 0, -exponent
	}
	switch {
	case total > maxDigits:
		return "", "max-digits:" + strconv.Itoa(maxDigits)
	case decimals > places:
		return "", "max-decimal-places:" + strconv.Itoa(places)
	case wholeDigits > maxDigits-places:
		return "", "max-whole-digits:" + strconv.Itoa(maxDigits-places)
	}
	// Quantize to exactly `places` decimals (no rounding is needed after the
	// decimal-places check).
	scaled := new(big.Int)
	scaled.SetString(coefficient, 10)
	scaled.Mul(scaled, new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(exponent+places)), nil))
	digitsText := scaled.String()
	for len(digitsText) <= places {
		digitsText = "0" + digitsText
	}
	result := digitsText[:len(digitsText)-places] + "." + digitsText[len(digitsText)-places:]
	if sign == "-" {
		result = "-" + result
	}
	return result, ""
}
