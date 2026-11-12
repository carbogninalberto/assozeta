package dto

import (
	"encoding/json"
	"math"
	"math/big"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// Validation codes of create_subscription's serializers (rendered by the handler).
const (
	SubscriptionCreateDateFormat = "date-format:DD/MM/YYYY"
	SubscriptionCreateBoolean    = "boolean"
	SubscriptionCreateNoData     = "no-data"
	SubscriptionCreateSignature  = "La firma è obbligatoria."
)

// PythonFloatText is repr() of a float.
func PythonFloatText(value float64) string { return pythonFloatRepr(value) }

// SubscriptionCreateDecimal is a float stored in a DecimalField(max_digits=9,
// decimal_places=2) whose decimal (nine significant digits, halves even) is
// already exact at two places. ok is false otherwise, for callers that want
// only amounts the column stores unchanged.
func SubscriptionCreateDecimal(value float64) (string, bool) {
	text, ok := SubscriptionCreateDecimalValue(value)
	if !ok {
		return "", false
	}
	rational, ok := new(big.Rat).SetString(text)
	if !ok {
		return "", false
	}
	if !new(big.Rat).Mul(rational, big.NewRat(100, 1)).IsInt() {
		return "", false
	}
	return rational.FloatString(2), true
}

// SubscriptionCreateDecimalValue is DecimalField(max_digits=9,
// decimal_places=2).to_python of a float: the decimal Django sends for the
// column, Context(prec=9).create_decimal_from_float (nine significant digits,
// halves even). ok is false when that conversion raises, for inf and nan.
func SubscriptionCreateDecimalValue(value float64) (string, bool) {
	if math.IsInf(value, 0) || math.IsNaN(value) {
		return "", false
	}
	return DecimalFromFloat9(value), true
}

// SubscriptionCreateDecimalStored is the numeric(9,2) value of that decimal:
// PostgreSQL rounds the decimal it receives to two places, halves away from
// zero (the stored column value auditlog later reads back).
func SubscriptionCreateDecimalStored(value float64) (string, bool) {
	text, ok := SubscriptionCreateDecimalValue(value)
	if !ok {
		return "", false
	}
	rational, ok := new(big.Rat).SetString(text)
	if !ok {
		return "", false
	}
	return rational.FloatString(2), true
}

// SubscriptionCreateBornDate is DRF's DateField with DATE_INPUT_FORMATS ['%d/%m/%Y'] (Python's strptime, which reads the decimal
// digits of other scripts where its pattern has \d).
func SubscriptionCreateBornDate(text string) (time.Time, bool) {
	date, ok, err := ParseDMYDate(text)
	return date, ok && err == nil
}

// SignatureRequest is SignatureRequestSerializer's validated data.
type SignatureRequest struct {
	Signed bool
	Data   *string
}

// ParseSignatureRequest is SignatureRequestSerializer(data=value).is_valid():
// field errors, then validate()'s "La firma è obbligatoria.".
func ParseSignatureRequest(value any) (SignatureRequest, map[string][]string) {
	var request SignatureRequest
	object, ok := value.(domain.RequestObject)
	if !ok {
		if value == nil {
			return request, map[string][]string{"non_field_errors": {SubscriptionCreateNoData}}
		}
		return request, map[string][]string{"non_field_errors": {"dictionary:" + pythonTypeName(value)}}
	}
	errors := map[string][]string{}
	if flag, present := object.Lookup("there_is_signature"); present {
		parsed, code := drfBoolean(flag, false)
		if code != "" {
			errors["there_is_signature"] = []string{SubscriptionCreateBoolean}
		} else {
			request.Signed = *parsed
		}
	}
	data, present := object.Lookup("data")
	switch v := data.(type) {
	case nil:
		if !present {
			errors["data"] = []string{PaymentCategoryValidationRequired}
		}
	case string:
		text := PythonStrip(v)
		request.Data = &text
	case json.Number:
		text := PythonStr(v)
		request.Data = &text
	default:
		errors["data"] = []string{PaymentCategoryValidationString}
	}
	if len(errors) > 0 {
		return request, errors
	}
	if request.Signed && (request.Data == nil || *request.Data == "") {
		return request, map[string][]string{"data": {SubscriptionCreateSignature}}
	}
	return request, nil
}

// SubscriptionCreateText is the text a CharField lookup compares (str() of a
// scalar); ok is false for values whose lookup is not ported.
func SubscriptionCreateText(value any) (*string, bool) {
	switch v := value.(type) {
	case nil:
		return nil, true
	case string:
		return &v, true
	case json.Number:
		text := PythonStr(v)
		return &text, true
	case bool:
		text := "False"
		if v {
			text = "True"
		}
		return &text, true
	}
	return nil, false
}

// SubscriptionCreateFloat is float(value) for a parsed JSON value: bool, a
// number and text (Python's float(), with surrounding whitespace, exponents,
// underscores, other-script digits, inf and nan) are accepted. server reports
// the TypeError/ValueError/OverflowError Django's view does not handle.
func SubscriptionCreateFloat(value any) (number float64, ok bool, server bool) {
	switch value.(type) {
	case bool, string, json.Number:
		number, ok = PythonFloatOf(value)
		return number, ok, false
	}
	return 0, true, true
}

// SubscriptionCreateInt is int(str(value).strip()); ok is false when it raises.
func SubscriptionCreateInt(value any) (*big.Int, bool) {
	switch v := value.(type) {
	case string:
		return pythonIntString(v)
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return nil, false
		}
		return new(big.Int).SetString(string(v), 10)
	}
	return nil, false
}

// SubscriptionCreateUserText is a UserSerializer model CharField/EmailField value.
func SubscriptionCreateUserText(field SupplierField, value any) (*string, []string) {
	return modelTextValue(field, value, false)
}
