package dto

import (
	"regexp"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// SubscriptionSignatureNoData is the root serializer's 'null' error, which
// Serializer.errors renders as {'non_field_errors': ['No data provided']}.
const SubscriptionSignatureNoData = "no-data"

// SubscriptionSignatureRequired is SignatureRequestSerializer.validate's
// {'data': 'La firma è obbligatoria.'} (a literal Italian message).
const SubscriptionSignatureRequired = "signature-required"

// SubscriptionSignature is SignatureRequestSerializer's validated data.
type SubscriptionSignature struct {
	There bool
	Data  *string // the trimmed text, None for null
}

// SubscriptionSignatureField is one field's error codes, in declaration order.
type SubscriptionSignatureField struct {
	Name  string
	Codes []string
}

// ParseSubscriptionSignature ports SignatureRequestSerializer(data=value).is_valid():
// there_is_signature BooleanField(default=False), data CharField(allow_blank=True,
// allow_null=True) (required), then validate().
func ParseSubscriptionSignature(value any) (SubscriptionSignature, []SubscriptionSignatureField) {
	if value == nil {
		return SubscriptionSignature{}, []SubscriptionSignatureField{{Name: "non_field_errors", Codes: []string{SubscriptionSignatureNoData}}}
	}
	object, ok := value.(domain.RequestObject)
	if !ok {
		return SubscriptionSignature{}, []SubscriptionSignatureField{{Name: "non_field_errors",
			Codes: []string{PaymentCategoryValidationDict + ":" + pythonTypeName(value)}}}
	}
	var result SubscriptionSignature
	var invalid []SubscriptionSignatureField
	if raw, present := object.Lookup("there_is_signature"); present {
		if raw == nil {
			invalid = append(invalid, SubscriptionSignatureField{Name: "there_is_signature", Codes: []string{PaymentCategoryValidationNull}})
		} else if flag, err := parsePaymentCategoryBoolean(raw); err != nil {
			invalid = append(invalid, SubscriptionSignatureField{Name: "there_is_signature", Codes: []string{PaymentCategoryValidationBoolean}})
		} else {
			result.There = flag
		}
	}
	if raw, present := object.Lookup("data"); !present {
		invalid = append(invalid, SubscriptionSignatureField{Name: "data", Codes: []string{PaymentCategoryValidationRequired}})
	} else if text, codes := modelTextValue(SupplierField{Name: "data", Null: true, Blank: true}, raw, false); len(codes) > 0 {
		invalid = append(invalid, SubscriptionSignatureField{Name: "data", Codes: codes})
	} else {
		result.Data = text
	}
	if len(invalid) > 0 {
		return SubscriptionSignature{}, invalid
	}
	if result.There && (result.Data == nil || *result.Data == "") {
		return SubscriptionSignature{}, []SubscriptionSignatureField{{Name: "data", Codes: []string{SubscriptionSignatureRequired}}}
	}
	return result, nil
}

var subscriptionSignatureBase64 = regexp.MustCompile(`\A(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?\z`)

// SubscriptionSignatureBase64 reports whether base64.b64decode(text, validate=True)
// accepts text (str input: ASCII only; strict mode: only the alphabet, complete
// quanta and padding at the end; the unused bits of the last quantum are ignored).
func SubscriptionSignatureBase64(text string) bool {
	return subscriptionSignatureBase64.MatchString(text)
}
