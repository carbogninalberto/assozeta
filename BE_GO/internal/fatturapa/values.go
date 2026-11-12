package fatturapa

import (
	"bytes"
	"encoding/json"
	"math"
	"math/big"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// pyInt and pyFloat are what Python's json module makes of a number.
type pyInt struct{ value *big.Int }
type pyFloat float64

func zeroInt() *big.Int { return new(big.Int) }

// parseJSON is json.loads of the jsonb text: objects map[string]any (the last
// duplicate wins), arrays []any, strings, bools, nil, pyInt and pyFloat.
func parseJSON(text string) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader([]byte(text)))
	decoder.UseNumber()
	var raw any
	if err := decoder.Decode(&raw); err != nil {
		return nil, ErrUnsupported
	}
	return convertJSON(raw)
}

func convertJSON(value any) (any, error) {
	switch v := value.(type) {
	case json.Number:
		text := string(v)
		if strings.ContainsAny(text, ".eE") {
			number, err := strconv.ParseFloat(text, 64)
			if err != nil || math.IsInf(number, 0) {
				return nil, ErrUnsupported
			}
			return pyFloat(number), nil
		}
		number, ok := new(big.Int).SetString(text, 10)
		if !ok {
			return nil, ErrUnsupported
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
	case map[string]any:
		for key, item := range v {
			converted, err := convertJSON(item)
			if err != nil {
				return nil, err
			}
			v[key] = converted
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
	case map[string]any:
		return len(v) > 0
	}
	return true
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
		if len(text) > 4300 {
			return nil, ErrUnsupported // int max str digits
		}
	case pyFloat:
		text = dto.PythonFloatStr(float64(v))
	default:
		return nil, ErrUnsupported // str() of a list or dict
	}
	return &text, nil
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
		result = decimalOfFloat(float64(v))
	case string:
		parsed, ok, err := parseDecimal(v)
		if err != nil {
			return nil, err
		}
		if !ok {
			return nil, ErrInvalid
		}
		result = parsed
	case []any:
		if len(v) == 3 {
			return nil, ErrUnsupported // a (sign, digits, exponent) sequence
		}
		return nil, ErrInvalid
	default:
		return nil, ErrInvalid // TypeError
	}
	return &result, nil
}
