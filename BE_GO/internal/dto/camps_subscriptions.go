package dto

import (
	"encoding/json"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// CampsValidationChoice prefixes ChoiceField's invalid_choice code with
// str(input).
const CampsValidationChoice = "invalid-choice:"

// CampSubscriptionInput is CampsAndRetreatsSubscriptionInfoSerializer's
// field-level validation; Errors holds codes by field, the service adds the
// lookups.
type CampSubscriptionInput struct {
	ID           CampsRelation
	Status       CampsValue[int64]
	CreatedAt    CampsValue[time.Time]
	Camp         CampsRelation
	Registration CampsRelation
	Errors       map[string][]string
}

// CampSubscriptionFieldOrder is the serializer's writable fields: the pk,
// model fields, then forward relations (periods is read-only).
var CampSubscriptionFieldOrder = []string{"camps_and_retreats_subscription_id", "status_flag", "created_at", "camps_and_retreats",
	"subscription"}

// CampSubscriptionPayload is request.data.copy() with the path uid assigned
// to camps_and_retreats: only a mapping (or a form QueryDict) has copy().
func CampSubscriptionPayload(body any, form bool, uid string) (domain.RequestObject, error) {
	switch data := body.(type) {
	case domain.RequestObject:
		copied := append(domain.RequestObject(nil), data...)
		for index, field := range copied {
			if _, upload := field.Value.(domain.RequestUpload); upload && form {
				// DRF merges files into the copied QueryDict (not ported).
				return nil, ErrCampsFormNotPorted
			}
			if field.Name == "camps_and_retreats" {
				copied[index].Value = uid
			}
		}
		if _, present := copied.Lookup("camps_and_retreats"); !present {
			copied = append(copied, domain.RequestField{Name: "camps_and_retreats", Value: uid})
		}
		return copied, nil
	case []any:
		// list.copy() succeeds; the item assignment does not.
		return nil, &CampsPythonError{Message: "list indices must be integers or slices, not str"}
	}
	return nil, &CampsPythonError{Message: "'" + pythonTypeName(body) + "' object has no attribute 'copy'"}
}

// ParseCampSubscriptionWrite ports the serializer's field validation of the
// payload (always a mapping).
func ParseCampSubscriptionWrite(object domain.RequestObject, form bool) *CampSubscriptionInput {
	reader := &campsReader{object: object, form: form, errors: map[string][]string{}}
	input := &CampSubscriptionInput{}
	input.ID = reader.uuid("camps_and_retreats_subscription_id")
	if value, present := reader.value("status_flag", false, false, false); present {
		switch {
		case value == nil:
			reader.errors["status_flag"] = []string{PaymentCategoryValidationNull}
		default:
			// choice_strings_to_values[str(data)]
			key := PythonStr(value)
			if status, err := strconv.ParseInt(key, 10, 64); err == nil && key == strconv.FormatInt(status, 10) && status >= 1 && status <= 3 {
				input.Status = CampsValue[int64]{Present: true, Value: status}
			} else {
				reader.errors["status_flag"] = []string{CampsValidationChoice + key}
			}
		}
	}
	input.CreatedAt = reader.datetime("created_at", false)
	input.Camp = reader.relation("camps_and_retreats", true, false)
	input.Registration = reader.relation("subscription", false, true)
	input.Errors = reader.errors
	return input
}

// CampsUUIDValue is uuid.UUID(int=...) for an int (a bool is one) or
// uuid.UUID(hex=...) for a str, as DRF's UUIDField.to_internal_value and
// Django's UUIDField.to_python run them; ok is false for anything else.
func CampsUUIDValue(value any, hex func(string) (string, bool)) (string, bool) {
	switch v := value.(type) {
	case bool:
		return supplierUUID(json.Number(map[bool]string{true: "1", false: "0"}[v]))
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return "", false
		}
		return supplierUUID(v)
	case string:
		return hex(v)
	}
	return "", false
}

// CampsInvalidUUIDMessage is str(django ValidationError) of UUIDField.to_python.
func CampsInvalidUUIDMessage(value any) string {
	return "[" + PythonStringRepr("“"+PythonStr(value)+"” is not a valid UUID.") + "]"
}

// CampsPythonSubscript is value[key] of a request value: dicts look the key
// up (KeyError when absent), other types raise their TypeError.
func CampsPythonSubscript(value any, key string) (any, error) {
	switch data := value.(type) {
	case domain.RequestObject:
		item, present := data.Lookup(key)
		if !present {
			return nil, &CampsPythonError{Message: PythonStringRepr(key)}
		}
		return item, nil
	case string:
		return nil, &CampsPythonError{Message: "string indices must be integers, not 'str'"}
	case []any:
		return nil, &CampsPythonError{Message: "list indices must be integers or slices, not str"}
	}
	return nil, &CampsPythonError{Message: "'" + pythonTypeName(value) + "' object is not subscriptable"}
}

// CampsPythonIterate is `for item in value` over a request value.
func CampsPythonIterate(value any) ([]any, error) {
	switch data := value.(type) {
	case []any:
		return data, nil
	case domain.RequestObject:
		items := make([]any, 0, len(data))
		for _, field := range data {
			items = append(items, field.Name)
		}
		return items, nil
	case string:
		items := []any{}
		for _, r := range pythonCodePoints(data) {
			items = append(items, string(r))
		}
		return items, nil
	}
	return nil, &CampsPythonError{Message: "'" + pythonTypeName(value) + "' object is not iterable"}
}

// CampsItalianAmount is locale.format_string('%.2f', value, grouping=True)
// under it_IT.UTF-8 (thousands '.', decimals ',').
func CampsItalianAmount(value float64) string {
	text := strconv.FormatFloat(value, 'f', 2, 64)
	sign := ""
	if strings.HasPrefix(text, "-") {
		sign, text = "-", text[1:]
	}
	whole, fraction, _ := strings.Cut(text, ".")
	var groups []string
	for len(whole) > 3 {
		groups = append([]string{whole[len(whole)-3:]}, groups...)
		whole = whole[:len(whole)-3]
	}
	groups = append([]string{whole}, groups...)
	return sign + strings.Join(groups, ".") + "," + fraction
}

// CampsMetaItem is one get_meta_payment_categories item.
func CampsMetaItem(category, serviceID string, title *string, amount float64) domain.RequestObject {
	var titleValue any
	if title != nil {
		titleValue = *title
	}
	return domain.RequestObject{{Name: "payment_category_id", Value: category}, {Name: "amount", Value: json.Number(pythonFloatRepr(amount))},
		{Name: "subject", Value: json.Number("0")}, {Name: "title", Value: titleValue},
		{Name: "camps_and_retreats_period_service_id", Value: serviceID}}
}

// CampsPeriodNotes is create_period_notes for one subscription period.
func CampsPeriodNotes(periodTitle, campTitle, description *string, fee float64, titles []*string, amounts []float64) string {
	notes := "Iscrizione al periodo " + campsOptional(periodTitle) + " del campo " + campsOptional(campTitle)
	if description != nil && *description != "" {
		notes += "\nQuota di iscrizione: " + CampsItalianAmount(fee) + "€\n"
	}
	for index := range titles {
		notes += "\n" + campsOptional(titles[index]) + ": " + CampsItalianAmount(amounts[index]) + "€"
	}
	return notes
}

// CampsTypeName is type(value).__name__ of a request value.
func CampsTypeName(value any) string { return pythonTypeName(value) }

// CampsListPayment is PaymentSerializer for a subscription period's payment
// (the service refuses instructors and invoices).
func CampsListPayment(row domain.CourseSubscriptionPayment) map[string]any {
	return courseSubscriptionPayment(row)
}
