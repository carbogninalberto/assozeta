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

var subscriptionCreateDate = regexp.MustCompile(`^(3[01]|[12][0-9]|0[1-9]|[1-9]| [1-9])/(1[0-2]|0[1-9]|[1-9])/([0-9]{4})$`)

// SubscriptionCreateBornDate is DRF's DateField with DATE_INPUT_FORMATS
// ['%d/%m/%Y'] for an ASCII string (Python's strptime).
func SubscriptionCreateBornDate(text string) (time.Time, bool) {
	match := subscriptionCreateDate.FindStringSubmatch(text)
	if match == nil {
		return time.Time{}, false
	}
	day, _ := strconv.Atoi(strings.TrimSpace(match[1]))
	month, _ := strconv.Atoi(match[2])
	year, _ := strconv.Atoi(match[3])
	date := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
	if year < 1 || date.Day() != day || int(date.Month()) != month {
		return time.Time{}, false
	}
	return date, true
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
