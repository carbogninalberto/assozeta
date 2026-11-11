package dto

import (
	"encoding/json"
	"errors"
	"math/big"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// CampsPythonError is an exception the camps views turn into
// 500 {'error': str(e)}.
type CampsPythonError struct{ Message string }

func (e *CampsPythonError) Error() string { return e.Message }

// ErrCampsFormNotPorted refuses HTML form input this port does not model
// (QueryDict.getlist of the many-to-many services, file parts) before any
// write.
var ErrCampsFormNotPorted = refusal.New("camps form input is not ported")

// Validation codes beyond the shared payment-category message table.
const (
	CampsValidationMaxValue = "max-value:"
	CampsValidationNoData   = "no-data"
)

// CampsValue is a validated scalar of a ModelSerializer field.
type CampsValue[T any] struct {
	Present bool
	Value   T
}

// CampsRelation is a PrimaryKeyRelatedField input before its queryset lookup:
// Code is a to_internal_value failure, Null a None value.
type CampsRelation struct {
	Present bool
	Null    bool
	ID      string
	Code    string
}

// CampsMany is a ManyRelatedField input: Code is a field failure, Items the
// child relations in iteration order.
type CampsMany struct {
	Present bool
	Code    string
	Items   []CampsRelation
}

// CampsPeriodInput is CampsAndRetreatsPeriodSerializer's field-level
// validation; Errors holds codes by field, the service adds the lookups. The
// primary key is read-only: a key in the body is ignored.
type CampsPeriodInput struct {
	Start, End  CampsValue[time.Time]
	Title       CampsValue[*string]
	Description CampsValue[*string]
	// Fee is the quantized decimal text; validate_fee makes it a float.
	Fee       CampsValue[string]
	Max       CampsValue[*int64]
	CreatedAt CampsValue[time.Time]
	Camp      CampsRelation
	Services  CampsMany
	Errors    map[string][]string
}

// CampsServiceInput is CampsAndRetreatsPeriodsServiceAddSerializer's
// field-level validation (the read-only primary key is ignored).
type CampsServiceInput struct {
	Title       CampsValue[*string]
	Description CampsValue[*string]
	Fee         CampsValue[string]
	CreatedAt   CampsValue[time.Time]
	Period      CampsRelation
	Category    CampsRelation
	Errors      map[string][]string
}

// CampsPeriodFieldOrder and CampsServiceFieldOrder are the serializers'
// writable fields: model fields, then forward relations.
var (
	CampsPeriodFieldOrder = []string{"start_date", "end_date", "title", "description", "fee",
		"max_participants", "created_at", "camps_and_retreats", "camps_and_Retreats_period_services"}
	CampsServiceFieldOrder = []string{"title", "description", "fee", "created_at",
		"camps_and_retreats_period", "payment_category"}
)

const campsServicesField = "camps_and_Retreats_period_services"

type campsReader struct {
	object  domain.RequestObject
	form    bool
	partial bool
	errors  map[string][]string
}

// value is Field.get_value plus the empty/None handling of
// validate_empty_values: skip reports a SkipField.
func (r *campsReader) value(name string, required, null, blank bool) (any, bool) {
	value, present := r.object.Lookup(name)
	if !present {
		if !r.partial && required {
			r.errors[name] = []string{PaymentCategoryValidationRequired}
		}
		return nil, false
	}
	if r.form && value == "" {
		switch {
		case null && blank:
			return "", true
		case null:
			return nil, true
		case !required:
			return nil, false
		}
	}
	return value, true
}

func (r *campsReader) uuid(name string) CampsRelation {
	value, present := r.value(name, false, false, false)
	if !present {
		return CampsRelation{}
	}
	if value == nil {
		r.errors[name] = []string{PaymentCategoryValidationNull}
		return CampsRelation{}
	}
	if flag, ok := value.(bool); ok {
		// UUIDField takes a bool as the int it is: UUID(int=True).
		value = json.Number(map[bool]string{true: "1", false: "0"}[flag])
	}
	if number, ok := value.(json.Number); ok && strings.ContainsAny(string(number), ".eE") {
		r.errors[name] = []string{PaymentCategoryValidationUUID}
		return CampsRelation{}
	}
	id, ok := supplierUUID(value)
	if !ok {
		r.errors[name] = []string{PaymentCategoryValidationUUID}
		return CampsRelation{}
	}
	return CampsRelation{Present: true, ID: id}
}

func (r *campsReader) datetime(name string, required bool) CampsValue[time.Time] {
	value, present := r.value(name, required, false, false)
	if !present {
		return CampsValue[time.Time]{}
	}
	if value == nil {
		r.errors[name] = []string{PaymentCategoryValidationNull}
		return CampsValue[time.Time]{}
	}
	text, ok := value.(string)
	if !ok {
		r.errors[name] = []string{PaymentCategoryValidationDateTime}
		return CampsValue[time.Time]{}
	}
	parsed, err := parsePaymentCategoryDjangoDateTime(text)
	switch {
	case errors.Is(err, errPaymentCategoryDateTimeOverflow):
		r.errors[name] = []string{PaymentCategoryValidationDateTimeOverflow}
	case err != nil:
		r.errors[name] = []string{PaymentCategoryValidationDateTime}
	default:
		return CampsValue[time.Time]{Present: true, Value: parsed.UTC()}
	}
	return CampsValue[time.Time]{}
}

func (r *campsReader) text(name string, maximum int) CampsValue[*string] {
	value, present := r.value(name, false, true, false)
	if !present {
		return CampsValue[*string]{}
	}
	text, codes := modelTextValue(SupplierField{Name: name, Max: maximum, Null: true}, value, false)
	if len(codes) > 0 {
		r.errors[name] = codes
		return CampsValue[*string]{}
	}
	return CampsValue[*string]{Present: true, Value: text}
}

func (r *campsReader) decimal(name string) CampsValue[string] {
	value, present := r.value(name, false, false, false)
	if !present {
		return CampsValue[string]{}
	}
	if value == nil {
		r.errors[name] = []string{PaymentCategoryValidationNull}
		return CampsValue[string]{}
	}
	text, code := DRFDecimal(value, 9, 2)
	if code != "" {
		r.errors[name] = []string{code}
		return CampsValue[string]{}
	}
	return CampsValue[string]{Present: true, Value: text}
}

func (r *campsReader) relation(name string, required, null bool) CampsRelation {
	value, present := r.value(name, required, null, false)
	if !present {
		return CampsRelation{}
	}
	if value == "" {
		// RelatedField.run_validation forces '' to None.
		value = nil
	}
	if value == nil {
		if !null {
			r.errors[name] = []string{PaymentCategoryValidationNull}
			return CampsRelation{}
		}
		return CampsRelation{Present: true, Null: true}
	}
	return campsRelated(value)
}

// campsRelated is PrimaryKeyRelatedField.to_internal_value up to the lookup.
func campsRelated(value any) CampsRelation {
	if value == nil {
		// queryset.get(pk=None) matches nothing.
		return CampsRelation{Present: true, ID: "None", Code: "related-does-not-exist:None"}
	}
	id, code := supplierRelated(value)
	if code != "" {
		return CampsRelation{Present: true, Code: code}
	}
	return CampsRelation{Present: true, ID: id}
}

func campsInput(body any, form bool) (*campsReader, error) {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return nil, nil
	}
	return &campsReader{object: object, form: form, errors: map[string][]string{}}, nil
}

// campsNonDict is Serializer.to_internal_value's non-mapping failure, after
// run_validation's None check ('No data provided').
func campsNonDict(body any) map[string][]string {
	if body == nil {
		return map[string][]string{"non_field_errors": {CampsValidationNoData}}
	}
	return map[string][]string{"non_field_errors": {"dictionary:" + pythonTypeName(body)}}
}

// campsPeriodPreprocess is CampsAndRetreatsPeriodSerializer.to_internal_value's
// "'fee' in data and data['fee'] == ”" for each key before validation.
func campsPeriodPreprocess(body any, form bool) (domain.RequestObject, error) {
	for _, key := range []string{"fee", "max_participants"} {
		switch data := body.(type) {
		case nil:
			return nil, nil
		case domain.RequestObject:
			value, present := data.Lookup(key)
			if present && value == "" {
				if form && !data.HasUpload() {
					// Only a form with files is a mutable copy.
					return nil, &CampsPythonError{Message: "This QueryDict instance is immutable"}
				}
				copied := append(domain.RequestObject(nil), data...)
				for index := range copied {
					if copied[index].Name == key {
						copied[index].Value = nil
					}
				}
				body = copied
			}
		case string:
			if strings.Contains(data, key) {
				return nil, &CampsPythonError{Message: "string indices must be integers, not 'str'"}
			}
		case []any:
			for _, item := range data {
				if item == key {
					return nil, &CampsPythonError{Message: "list indices must be integers or slices, not str"}
				}
			}
		default:
			return nil, &CampsPythonError{Message: "argument of type '" + pythonTypeName(body) + "' is not iterable"}
		}
	}
	object, _ := body.(domain.RequestObject)
	return object, nil
}

// ParseCampsPeriodWrite ports CampsAndRetreatsPeriodSerializer(data=...,
// partial=...) up to the queryset lookups. A nil input with nil error is a
// non-mapping body whose errors are in the returned map.
func ParseCampsPeriodWrite(body any, form, partial bool) (*CampsPeriodInput, map[string][]string, error) {
	if body == nil {
		return nil, campsNonDict(nil), nil
	}
	object, err := campsPeriodPreprocess(body, form)
	if err != nil {
		return nil, nil, err
	}
	if object == nil {
		return nil, campsNonDict(body), nil
	}
	reader, err := campsInput(object, form)
	if err != nil {
		return nil, nil, err
	}
	reader.partial = partial
	input := &CampsPeriodInput{}
	input.Start = reader.datetime("start_date", true)
	input.End = reader.datetime("end_date", true)
	input.Title = reader.text("title", 255)
	input.Description = reader.text("description", 2048)
	input.Fee = reader.decimal("fee")
	if value, present := reader.value("max_participants", false, true, false); present {
		input.Max = campsInteger(reader, value)
	}
	input.CreatedAt = reader.datetime("created_at", false)
	input.Camp = reader.relation("camps_and_retreats", true, false)
	if value, present := object.Lookup(campsServicesField); present && form {
		// ManyRelatedField.get_value: getlist() of an HTML key.
		list, _ := object.List(campsServicesField)
		input.Services = campsMany(reader, list)
	} else if present {
		input.Services = campsMany(reader, value)
	} else if form && !partial {
		// ManyRelatedField.get_value: getlist() of a missing HTML key is [].
		input.Services = CampsMany{Present: true}
	}
	input.Errors = reader.errors
	return input, nil, nil
}

// campsInteger is the max_participants IntegerField (min 0, max 2147483647,
// nullable) then validate_max_participants.
func campsInteger(reader *campsReader, value any) CampsValue[*int64] {
	const name = "max_participants"
	if value == nil {
		return CampsValue[*int64]{Present: true}
	}
	number, code := DRFInteger(value)
	if code != "" {
		reader.errors[name] = []string{code}
		return CampsValue[*int64]{}
	}
	switch {
	case number.Cmp(big.NewInt(2147483647)) > 0:
		reader.errors[name] = []string{CampsValidationMaxValue + "2147483647"}
		return CampsValue[*int64]{}
	case number.Sign() < 0:
		reader.errors[name] = []string{"min-value:0"}
		return CampsValue[*int64]{}
	}
	result := number.Int64()
	return CampsValue[*int64]{Present: true, Value: &result}
}

// campsMany is ManyRelatedField.run_validation: None fails, a str or a
// non-iterable is not a list, a dict iterates its keys.
func campsMany(reader *campsReader, value any) CampsMany {
	var items []any
	switch data := value.(type) {
	case nil:
		reader.errors[campsServicesField] = []string{PaymentCategoryValidationNull}
		return CampsMany{}
	case []any:
		items = data
	case domain.RequestObject:
		for _, field := range data {
			items = append(items, field.Name)
		}
	default:
		reader.errors[campsServicesField] = []string{"not-a-list:" + pythonTypeName(value)}
		return CampsMany{}
	}
	many := CampsMany{Present: true}
	for _, item := range items {
		many.Items = append(many.Items, campsRelated(item))
	}
	return many
}

// ParseCampsServiceWrite ports CampsAndRetreatsPeriodsServiceAddSerializer.
func ParseCampsServiceWrite(body any, form, partial bool) (*CampsServiceInput, map[string][]string, error) {
	if body == nil {
		return nil, campsNonDict(nil), nil
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		return nil, campsNonDict(body), nil
	}
	reader, err := campsInput(object, form)
	if err != nil {
		return nil, nil, err
	}
	reader.partial = partial
	input := &CampsServiceInput{}
	input.Title = reader.text("title", 255)
	input.Description = reader.text("description", 2048)
	input.Fee = reader.decimal("fee")
	input.CreatedAt = reader.datetime("created_at", false)
	input.Period = reader.relation("camps_and_retreats_period", true, false)
	input.Category = reader.relation("payment_category", false, true)
	input.Errors = reader.errors
	return input, nil, nil
}

// CampsDRFCode is the ErrorDetail code of a validation code.
func CampsDRFCode(code string) string {
	switch {
	case code == PaymentCategoryValidationRequired, code == PaymentCategoryValidationNull, code == PaymentCategoryValidationBlank:
		return code
	case code == CampsValidationNoData:
		return "null"
	case code == PaymentCategoryValidationDateTimeOverflow:
		return "overflow"
	case code == DRFValidationMaxStringLength:
		return "max_string_length"
	case strings.HasPrefix(code, "max-digits:"):
		return "max_digits"
	case strings.HasPrefix(code, "max-decimal-places:"):
		return "max_decimal_places"
	case strings.HasPrefix(code, "max-whole-digits:"):
		return "max_whole_digits"
	case strings.HasPrefix(code, "min-value:"):
		return "min_value"
	case strings.HasPrefix(code, CampsValidationMaxValue):
		return "max_value"
	case strings.HasPrefix(code, "max:"):
		return "max_length"
	case code == "null-character":
		return "null_characters_not_allowed"
	case strings.HasPrefix(code, "surrogate:"):
		return "surrogate_characters_not_allowed"
	case strings.HasPrefix(code, "unique:"):
		return "unique"
	case strings.HasPrefix(code, "related-does-not-exist:"):
		return "does_not_exist"
	case strings.HasPrefix(code, "related-incorrect-type:"):
		return "incorrect_type"
	case strings.HasPrefix(code, "not-a-list:"):
		return "not_a_list"
	}
	// uuid, string, datetime, number, integer, related-invalid-uuid and the
	// non-mapping body.
	return "invalid"
}

// CampsFloatRepr is str(float(decimal text)), the in-memory value
// validate_fee leaves on the instance.
func CampsFloatRepr(text string) string {
	value, _ := strconv.ParseFloat(text, 64)
	return pythonFloatRepr(value)
}

// CampsFloatDecimal is DRF DecimalField.to_representation of that float:
// Decimal(str(value)) quantized to two places ('-0.0' keeps its sign).
func CampsFloatDecimal(text string) string {
	repr := CampsFloatRepr(text)
	quantized, _ := DRFDecimal(repr, 40, 2)
	if strings.HasPrefix(repr, "-") && !strings.HasPrefix(quantized, "-") {
		quantized = "-" + quantized
	}
	return quantized
}

// CampsCommaFee is str(Decimal).replace('.', ',').
func CampsCommaFee(text string) string { return strings.ReplaceAll(text, ".", ",") }

// CampsNaiveUTC is auditlog's str() of a naive UTC datetime.
func CampsNaiveUTC(value time.Time) string {
	value = value.UTC()
	if value.Nanosecond()/1000 == 0 {
		return value.Format("2006-01-02 15:04:05")
	}
	return value.Format("2006-01-02 15:04:05.000000")
}
