package djtemplate

import (
	"bytes"
	"encoding/json"
	"math"
	"strconv"
	"strings"
)

// FromJSON is json.loads of a JSON column's text as Django returns it: dicts
// keep the text's key order, integers are int (beyond int64 refused) and
// numbers with a fraction or exponent are float.
func FromJSON(raw []byte) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	value, err := decodeJSON(decoder)
	if err != nil {
		return nil, err
	}
	if decoder.More() {
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
		text := string(v)
		if !strings.ContainsAny(text, ".eE") {
			number, err := strconv.ParseInt(text, 10, 64)
			if err != nil {
				return nil, unsupported("JSON integer %s", text)
			}
			return number, nil
		}
		number, err := strconv.ParseFloat(text, 64)
		if err != nil || math.IsInf(number, 0) {
			return nil, unsupported("JSON float %s", text)
		}
		return number, nil
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
