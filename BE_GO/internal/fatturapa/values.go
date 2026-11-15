package fatturapa

import (
	"bytes"
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// pyInt and pyFloat are what Python's json module makes of a number.
type pyInt struct{ value *big.Int }
type pyFloat float64

// pyObject is what json.loads makes of an object: a Python dict, whose key
// order is the text's and whose repeated keys keep their first position with
// the last value ({"b": 1, "a": 2, "b": 3} is {'b': 3, 'a': 2}).
type pyObject []pyField

type pyField struct {
	key   string
	value any
}

func (o pyObject) get(key string) (any, bool) {
	for _, item := range o {
		if item.key == key {
			return item.value, true
		}
	}
	return nil, false
}

func (o *pyObject) set(key string, value any) {
	for i := range *o {
		if (*o)[i].key == key {
			(*o)[i].value = value
			return
		}
	}
	*o = append(*o, pyField{key: key, value: value})
}

func zeroInt() *big.Int { return new(big.Int) }

var errJSONToken = errors.New("fatturapa: unexpected JSON token")

// parseJSON is json.loads of the jsonb text: objects pyObject, arrays []any,
// strings, bools, nil, pyInt and pyFloat.
func parseJSON(text string) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader([]byte(text)))
	decoder.UseNumber()
	value, err := decodeJSON(decoder)
	if err != nil {
		return nil, ErrUnexpectedJSON // jsonb holds valid JSON
	}
	return convertJSON(value)
}

// decodeJSON reads one value, keeping an object's key order.
func decodeJSON(decoder *json.Decoder) (any, error) {
	token, err := decoder.Token()
	if err != nil {
		return nil, err
	}
	delim, ok := token.(json.Delim)
	if !ok {
		return token, nil // string, json.Number, bool or nil
	}
	switch delim {
	case '{':
		object := pyObject{}
		for decoder.More() {
			keyToken, err := decoder.Token()
			if err != nil {
				return nil, err
			}
			key, ok := keyToken.(string)
			if !ok {
				return nil, errJSONToken
			}
			value, err := decodeJSON(decoder)
			if err != nil {
				return nil, err
			}
			object.set(key, value)
		}
		if _, err := decoder.Token(); err != nil { // the closing brace
			return nil, err
		}
		return object, nil
	case '[':
		array := []any{}
		for decoder.More() {
			item, err := decodeJSON(decoder)
			if err != nil {
				return nil, err
			}
			array = append(array, item)
		}
		if _, err := decoder.Token(); err != nil { // the closing bracket
			return nil, err
		}
		return array, nil
	}
	return nil, errJSONToken
}

func convertJSON(value any) (any, error) {
	switch v := value.(type) {
	case json.Number:
		text := string(v)
		if strings.ContainsAny(text, ".eE") {
			number, err := strconv.ParseFloat(text, 64)
			if err != nil {
				// json.loads is float(): a literal beyond float64 is +/-inf
				// (e.g. '1e400', or jsonb's 400-digit '1000...0.5').
				if numErr, ok := err.(*strconv.NumError); !ok || numErr.Err != strconv.ErrRange {
					return nil, ErrUnexpectedJSON // jsonb prints plain numbers
				}
			}
			return pyFloat(number), nil
		}
		number, ok := new(big.Int).SetString(text, 10)
		if !ok {
			return nil, ErrUnexpectedJSON // jsonb prints integers as plain digits
		}
		if len(strings.TrimPrefix(text, "-")) > 4300 {
			return nil, ErrUnexpectedJSON // json.loads raises: int max str digits
		}
		return pyInt{number}, nil
	case []any:
		for i, item := range v {
			converted, err := convertJSON(item)
			if err != nil {
				return nil, err
			}
			v[i] = converted
		}
		return v, nil
	case pyObject:
		for i, item := range v {
			converted, err := convertJSON(item.value)
			if err != nil {
				return nil, err
			}
			v[i].value = converted
		}
		return v, nil
	}
	return value, nil
}

func pyTruthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case pyInt:
		return v.value.Sign() != 0
	case pyFloat:
		return v != 0
	case string:
		return v != ""
	case []any:
		return len(v) > 0
	case pyObject:
		return len(v) > 0
	}
	return true
}

// pythonFloatStr is str(float): 'inf' and '-inf', where dto's Decimal
// spelling would be 'Infinity'.
func pythonFloatStr(value float64) string {
	switch text := dto.PythonFloatStr(value); text {
	case "Infinity":
		return "inf"
	case "-Infinity":
		return "-inf"
	case "NaN":
		return "nan" // json.loads never yields it
	default:
		return text
	}
}

// pythonValue converts a parsed value to the shapes dto.PythonStr reprs.
func pythonValue(value any) any {
	switch v := value.(type) {
	case pyInt:
		return json.Number(v.value.String())
	case pyFloat:
		return float64(v)
	case pyObject:
		object := make(domain.RequestObject, len(v))
		for i, item := range v {
			object[i] = domain.RequestField{Name: item.key, Value: pythonValue(item.value)}
		}
		return object
	case []any:
		items := make([]any, len(v))
		for i, item := range v {
			items[i] = pythonValue(item)
		}
		return items
	}
	return value // nil, bool and string repr themselves
}

// pyStr is StringField.clean_value: str(value), nil for None.
func pyStr(value any) (*string, error) {
	var text string
	switch v := value.(type) {
	case nil:
		return nil, nil
	case string:
		text = v
	case bool:
		text = "False"
		if v {
			text = "True"
		}
	case pyInt:
		text = v.value.String()
		if len(strings.TrimPrefix(text, "-")) > 4300 {
			return nil, ErrUnexpectedJSON // int max str digits: json.loads raised first
		}
	case pyFloat:
		text = pythonFloatStr(float64(v))
	default:
		// str() of a list or dict is its repr().
		text = dto.PythonStr(pythonValue(value))
	}
	return &text, nil
}

// pyInteger is Python's int-ness: an int, or a bool, which _decimal also
// accepts as a tuple member.
func pyInteger(value any) (*big.Int, bool) {
	switch v := value.(type) {
	case bool:
		if v {
			return big.NewInt(1), true
		}
		return new(big.Int), true
	case pyInt:
		return v.value, true
	}
	return nil, false
}

var (
	bigNine     = big.NewInt(9)
	bigOne      = big.NewInt(1)
	maxExponent = big.NewInt(999999999999999999)
	// MPD_MIN_ETINY = MPD_MIN_EMIN - MPD_MAX_PREC + 1.
	minAdjustedExponent = big.NewInt(-1999999999999999997)
)

// decimalFromTuple is Decimal((sign, digits, exponent)): _decimal's tuple
// constructor, which also accepts a list as the coefficient and a bool as an
// int. 'F', 'n' and 'N' build Infinity, NaN and sNaN, which the field then
// rejects.
func decimalFromTuple(items []any) (*decimal, error) {
	if len(items) != 3 {
		return nil, ErrInvalid // ValueError: a sequence of length 3
	}
	sign, ok := pyInteger(items[0])
	if !ok || sign.Sign() < 0 || sign.Cmp(bigOne) > 0 {
		return nil, ErrInvalid // ValueError: sign must be 0 or 1
	}
	coefficient, ok := items[1].([]any)
	if !ok {
		return nil, ErrInvalid // ValueError: coefficient must be a tuple of digits
	}
	coef := new(big.Int)
	for _, item := range coefficient {
		digit, ok := pyInteger(item)
		if !ok || digit.Sign() < 0 || digit.Cmp(bigNine) > 0 {
			return nil, ErrInvalid // ValueError: a coefficient digit is 0..9
		}
		coef.Mul(coef, bigTen)
		coef.Add(coef, digit)
	}
	exponent, ok := pyInteger(items[2])
	if !ok {
		return nil, ErrInvalid // ValueError or the special values above
	}
	value, ok := decimalFromParts(sign.Sign() == 1, coef, exponent)
	if !ok {
		return nil, ErrInvalid
	}
	return value, nil
}

// decimalFromParts applies libmpdec's exponent range to a coefficient and its
// exponent: the adjusted exponent must stay inside
// [MPD_MIN_ETINY, MPD_MAX_EMAX], else the constructor raises InvalidOperation.
func decimalFromParts(neg bool, coef, exponent *big.Int) (*decimal, bool) {
	adjusted := new(big.Int).Add(exponent, big.NewInt(int64(decimalDigits(coef)-1)))
	if adjusted.Cmp(minAdjustedExponent) < 0 || adjusted.Cmp(maxExponent) > 0 || !exponent.IsInt64() {
		return nil, false
	}
	return &decimal{neg: neg, coef: coef, exp: int(exponent.Int64())}, true
}

// pyDecimal is Decimal(value): nil for None.
func pyDecimal(value any) (*decimal, error) {
	var result decimal
	switch v := value.(type) {
	case nil:
		return nil, nil
	case bool:
		result = decimal{coef: zeroInt()}
		if v {
			result.coef = big.NewInt(1)
		}
	case pyInt:
		result = decimalOfInt(v.value)
	case pyFloat:
		number := float64(v)
		if math.IsInf(number, 0) || math.IsNaN(number) {
			// Decimal('Infinity') (Decimal.from_float of either infinity) has no
			// digit tuple: DecimalField.clean_value raises TypeError.
			return nil, ErrInvalid
		}
		result = decimalOfFloat(number)
	case string:
		parsed, ok := parseDecimal(v)
		if !ok {
			return nil, ErrInvalid
		}
		result = parsed
	case []any:
		return decimalFromTuple(v)
	default:
		return nil, ErrInvalid // TypeError
	}
	return &result, nil
}
