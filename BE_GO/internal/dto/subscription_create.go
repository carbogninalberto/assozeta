package dto

import (
	"encoding/json"
	"math/big"
	"regexp"
	"strconv"
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
// decimal_places=2): Django's context (9 significant digits, half even), then
// the column's two places. ok is false when the second rounding would change
// the value (the rounding modes of the two paths are not ported).
func SubscriptionCreateDecimal(value float64) (string, bool) {
	rational, ok := new(big.Rat).SetString(strconv.FormatFloat(value, 'e', 8, 64))
	if !ok {
		return "", false
	}
	if !new(big.Rat).Mul(rational, big.NewRat(100, 1)).IsInt() {
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

// SubscriptionCreateFloat is float(value) for a parsed JSON value. server
// reports Python's TypeError/ValueError; ok false refuses text whose float()
// is not ported (inf, nan, underscores, other spellings).
func SubscriptionCreateFloat(value any) (number float64, ok bool, server bool) {
	switch v := value.(type) {
	case bool:
		if v {
			return 1, true, false
		}
		return 0, true, false
	case json.Number:
		parsed, err := strconv.ParseFloat(string(v), 64)
		return parsed, err == nil, false
	case string:
		text := PythonStrip(v)
		if !subscriptionCreateFloatText.MatchString(text) {
			return 0, false, false
		}
		parsed, err := strconv.ParseFloat(text, 64)
		return parsed, err == nil, false
	}
	return 0, true, true
}

var subscriptionCreateFloatText = regexp.MustCompile(`^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?$`)

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
