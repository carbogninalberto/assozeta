package dto

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// MessageMutationWrite contains only MessageInputSerializer writable fields.
// IDs, association and timestamps are exclusively chosen by the service.
type MessageMutationWrite struct {
	Type, Message string
	Subject       *string
}

func ParseMessageMutationWrite(body any) (MessageMutationWrite, error) {
	var input MessageMutationWrite
	object, ok := body.(domain.RequestObject)
	if !ok {
		return input, domain.ErrMessageMutationInvalid
	}
	kind, present := object.Lookup("type")
	if !present {
		return input, domain.ErrMessageMutationInvalid
	}
	input.Type, ok = kind.(string)
	if !ok || (input.Type != "EMAIL" && input.Type != "INSIDE_APP") {
		return input, domain.ErrMessageMutationInvalid
	}
	value, present := object.Lookup("message")
	if !present {
		return input, domain.ErrMessageMutationInvalid
	}
	text, ok := messageMutationChar(value, math.MaxInt-2, false)
	if !ok {
		return input, domain.ErrMessageMutationInvalid
	}
	input.Message = text
	if value, present = object.Lookup("subject"); present && value != nil {
		text, ok = messageMutationChar(value, 255, true)
		if !ok {
			return input, domain.ErrMessageMutationInvalid
		}
		input.Subject = &text
	}
	return input, nil
}

// Reuse the shared DRF numeric coercion and Unicode validators. Guard strings
// with non-whitespace sentinels so trim_whitespace=False preserves every byte.
func messageMutationChar(value any, maximum int, blank bool) (string, bool) {
	if value == nil {
		return "", false
	}
	text, ok := value.(string)
	if !ok {
		var messages []string
		text, messages = parsePaymentCategoryString(value, maximum)
		if len(messages) > 0 {
			return "", false
		}
	}
	if text == "" {
		return "", blank
	}
	_, messages := parsePaymentCategoryString("x"+text+"x", maximum+2)
	return text, len(messages) == 0
}

// ParseMessageMutationsJSON applies Python's production integer limit before
// the shared code-unit parser. Validation must see ignored and overwritten keys.
func ParseMessageMutationsJSON(raw []byte) (any, error) {
	if _, err := parseReferenceJSON(raw, nil, true); err != nil {
		if errors.Is(err, ErrLoginRateJSONValue) {
			// The shared limit sentinel has no digit count. Recover the first
			// offending token only on this error path for DRF's parser detail.
			decoder := json.NewDecoder(bytes.NewReader(raw))
			decoder.UseNumber()
			for {
				token, scanErr := decoder.Token()
				if scanErr != nil {
					break
				}
				if n, ok := token.(json.Number); ok && !strings.ContainsAny(string(n), ".eE") {
					count := len(strings.TrimPrefix(string(n), "-"))
					if count > 4300 {
						return nil, &messageMutationIntegerError{digits: count}
					}
				}
			}
		}
		return nil, err
	}
	return ParseProfileTablesJSON(raw)
}

type messageMutationIntegerError struct{ digits int }

func (e *messageMutationIntegerError) Error() string {
	return fmt.Sprintf("Exceeds the limit (4300 digits) for integer string conversion: value has %d digits; use sys.set_int_max_str_digits() to increase the limit", e.digits)
}
func (e *messageMutationIntegerError) Unwrap() error { return ErrLoginRateJSONValue }
