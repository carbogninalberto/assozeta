package dto

import (
	"encoding/json"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// PaymentMiscTypeName is Python's type(value).__name__ for a parsed request value.
func PaymentMiscTypeName(value any) string { return pythonTypeName(value) }

// PaymentMiscCharField is serializers.CharField's to_internal_value with its
// validators (whitespace trimmed; numbers become their str()); the codes are
// the field's errors.
func PaymentMiscCharField(value any) (string, []string) {
	return parsePaymentCategoryString(value, int(^uint(0)>>1))
}

// PaymentMiscJSON renders a parsed stored JSON value as Python's json.loads
// then DRF's json.dumps would: floats in their repr, dictionaries as maps.
func PaymentMiscJSON(value any) any {
	switch v := value.(type) {
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return json.Number(pythonJSONNumber(string(v)))
		}
		return v
	case domain.RequestObject:
		result := make(map[string]any, len(v))
		for _, field := range v {
			result[field.Name] = PaymentMiscJSON(field.Value)
		}
		return result
	case []any:
		result := make([]any, len(v))
		for i, item := range v {
			result[i] = PaymentMiscJSON(item)
		}
		return result
	}
	return value
}
