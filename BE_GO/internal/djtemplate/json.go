package djtemplate

import (
	"bytes"
	"encoding/json"
	"errors"
	"math/big"
	"strconv"
	"strings"
)

// pythonIntDigits is sys.int_info.default_max_str_digits: int() (and so
// json.loads) refuses a decimal text longer than this with ValueError, which
// the view answers with Django's 500 page.
const pythonIntDigits = 4300

// FromJSON is json.loads of a JSON column's text as Django returns it: dicts
// keep the text's key order, integers are int (any length, up to Python's
// 4300-digit conversion limit, beyond int64 as *big.Int) and numbers with a
// fraction or exponent are float (Python's float(): a decimal that overflows
// is ±Inf, one that underflows is 0 or a subnormal).
func FromJSON(raw []byte) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	value, err := decodeJSON(decoder)
	if err != nil {
		return nil, err
	}
	if decoder.More() {
		// PostgreSQL's jsonb never writes trailing data; a text with more than
		// one value cannot be stored, so it stays refused.
		return nil, unsupported("trailing JSON")
	}
	return value, nil
}

func decodeJSON(decoder *json.Decoder) (any, error) {
	token, err := decoder.Token()
	if err != nil {
		return nil, unsupported("JSON: %v", err)
	}
	switch v := token.(type) {
	case nil:
		return nil, nil
	case bool:
		return v, nil
	case string:
		return v, nil
	case json.Number:
		return decodeJSONNumber(string(v))
	case json.Delim:
		switch v {
		case '[':
			list := List{}
			for decoder.More() {
				item, err := decodeJSON(decoder)
				if err != nil {
					return nil, err
				}
				list = append(list, item)
			}
			if _, err := decoder.Token(); err != nil {
				return nil, unsupported("JSON: %v", err)
			}
			return list, nil
		case '{':
			dict := NewDict()
			for decoder.More() {
				key, err := decoder.Token()
				if err != nil {
					return nil, unsupported("JSON: %v", err)
				}
				text, ok := key.(string)
				if !ok {
					return nil, unsupported("JSON key")
				}
				if _, duplicate := dict.Get(text); duplicate {
					// PostgreSQL's jsonb keeps the last value of a duplicated
					// key, so such a text cannot be stored (and json.loads
					// would keep the first): it stays refused.
					return nil, unsupported("duplicate JSON key")
				}
				item, err := decodeJSON(decoder)
				if err != nil {
					return nil, err
				}
				dict.Set(text, item)
			}
			if _, err := decoder.Token(); err != nil {
				return nil, unsupported("JSON: %v", err)
			}
			return dict, nil
		}
	}
	return nil, unsupported("JSON token %v", token)
}

// decodeJSONNumber is json.loads' parse_int/parse_float: a text without a
// fraction or exponent is int() (a ValueError beyond 4300 digits), the others
// float().
func decodeJSONNumber(text string) (any, error) {
	if !strings.ContainsAny(text, ".eE") {
		if digits := len(strings.TrimPrefix(text, "-")); digits > pythonIntDigits {
			return nil, pythonError("JSON integer of %d digits", digits)
		}
		number, ok := new(big.Int).SetString(text, 10)
		if !ok {
			return nil, unsupported("JSON integer %s", text)
		}
		if number.IsInt64() {
			return number.Int64(), nil
		}
		return number, nil
	}
	number, err := strconv.ParseFloat(text, 64)
	if err != nil {
		// Overflow is ±Inf, as float() gives it; underflow is 0 or a
		// subnormal and needs no special case in strconv.
		var rangeError *strconv.NumError
		if !errors.As(err, &rangeError) || !errors.Is(rangeError.Err, strconv.ErrRange) {
			return nil, unsupported("JSON float %s", text)
		}
	}
	return number, nil
}
