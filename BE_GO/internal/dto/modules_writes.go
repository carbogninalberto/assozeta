package dto

import (
	"reflect"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// ModuleFieldOrder is the writable field order of ModulesSerializer and
// ModulesAddSerializer (fields '__all__': the pk, the declared fields, then
// the model fields); DRF collects field errors in this order.
var ModuleFieldOrder = []string{"module_id", "sport_association", "custom_link", "creation_date", "title", "require_approval",
	"start_date", "end_date", "always_active", "max_responses", "queue_mode", "only_users", "payment_required", "payment_data",
	"response_message", "allow_attachments", "elements"}

// ModuleBooleanFields are Module's BooleanFields (not null, with defaults).
var ModuleBooleanFields = []string{"require_approval", "always_active", "queue_mode", "only_users", "payment_required", "allow_attachments"}

var moduleTextFields = map[string]SupplierField{
	"custom_link":      {Name: "custom_link", Max: 255, Null: true},
	"title":            {Name: "title", Max: 255, Null: true},
	"response_message": {Name: "response_message", Max: 1024, Null: true},
}

const (
	// ModuleUniqueID and ModuleUniqueLink are the UniqueValidator codes.
	ModuleUniqueID   = "unique:module:module id"
	ModuleUniqueLink = "unique:module:custom link"
	// ModuleDate is DateField's 'invalid' for DATE_INPUT_FORMATS ['%d/%m/%Y'].
	ModuleDate = "module-date"
	// ModuleJSON is JSONField's 'invalid' (HTML input that json.loads rejects).
	ModuleJSON = "module-json"
	// ModuleNestedDictionary prefixes the nested serializer's non-dict error.
	ModuleNestedDictionary = "nested-dictionary:"
)

// ModuleNested is what a PATCH carries for the nested sport_association.
type ModuleNested int

const (
	ModuleNestedAbsent ModuleNested = iota
	// ModuleNestedEmpty is a valid empty dict: ModelSerializer.update then
	// asserts on the writable nested field.
	ModuleNestedEmpty
	// ModuleNestedFields is a dict with keys: SportAssociationSerializer
	// validation of the nested fields is not ported.
	ModuleNestedFields
)

// moduleNestedFieldNames are the keys SportAssociationSerializer renders (its
// fields, the read-only ones included).
var moduleNestedFieldNames = func() map[string]bool {
	names := map[string]bool{}
	profile := reflect.TypeOf(AssociationProfile{})
	for index := 0; index < profile.NumField(); index++ {
		name, _, _ := strings.Cut(profile.Field(index).Tag.Get("json"), ",")
		if name != "" && name != "-" {
			names[name] = true
		}
	}
	return names
}()

// ModuleWrite is the validated data of the present fields.
type ModuleWrite struct {
	ID         *string
	Text       map[string]*string
	Created    *time.Time
	Booleans   map[string]bool
	Dates      map[string]*string
	MaxPresent bool
	Max        *int64
	// JSON holds the parsed JSON field values (request order kept).
	JSON   map[string]any
	Nested ModuleNested
}

// ModuleWriteErrors maps a field to its error codes.
type ModuleWriteErrors map[string][]string

// ParseModuleWrite ports the field validation of ModulesAddSerializer
// (nested false: the view overwrites sport_association) and of
// ModulesSerializer(partial=True) (nested true). Uniqueness is checked by the
// service. A non-dict body is the non_field_errors code.
func ParseModuleWrite(body any, form, nested bool) (ModuleWrite, ModuleWriteErrors) {
	write := ModuleWrite{Text: map[string]*string{}, Booleans: map[string]bool{}, Dates: map[string]*string{}, JSON: map[string]any{}}
	object, ok := body.(domain.RequestObject)
	if !ok {
		return write, ModuleWriteErrors{"non_field_errors": {"dictionary:" + pythonTypeName(body)}}
	}
	errors := ModuleWriteErrors{}
	lookup := func(name string) (any, bool) {
		value, present := object.Lookup(name)
		if present && form && value == "" && (name == "start_date" || name == "end_date" || name == "max_responses") {
			// Field.get_value: an empty HTML input of a nullable field is None.
			return nil, true
		}
		return value, present
	}
	if value, present := lookup("module_id"); present {
		if value == nil {
			errors["module_id"] = []string{PaymentCategoryValidationNull}
		} else if id, ok := drfUUID(value); ok {
			write.ID = &id
		} else {
			errors["module_id"] = []string{PaymentCategoryValidationUUID}
		}
	}
	if nested {
		if form {
			// Serializer.get_value on HTML input: only 'sport_association.<key>' keys.
			for _, field := range object {
				if strings.HasPrefix(field.Name, "sport_association.") {
					write.Nested = ModuleNestedFields
				}
			}
		} else if value, present := object.Lookup("sport_association"); present {
			switch v := value.(type) {
			case nil:
				errors["sport_association"] = []string{PaymentCategoryValidationNull}
			case domain.RequestObject:
				// Keys the nested serializer has no field for are dropped: the
				// validated value is the empty dict.
				write.Nested = ModuleNestedEmpty
				for _, field := range v {
					if moduleNestedFieldNames[field.Name] {
						write.Nested = ModuleNestedFields
					}
				}
			default:
				errors["sport_association"] = []string{ModuleNestedDictionary + pythonTypeName(value)}
			}
		}
	}
	for _, name := range []string{"custom_link", "title", "response_message"} {
		value, present := object.Lookup(name)
		if !present {
			continue
		}
		text, codes := modelTextValue(moduleTextFields[name], value, form)
		if len(codes) > 0 {
			errors[name] = codes
			continue
		}
		write.Text[name] = text
	}
	if value, present := lookup("creation_date"); present {
		if moment, code := drfDateTime(value, false); code != "" {
			errors["creation_date"] = []string{code}
		} else {
			write.Created = moment
		}
	}
	for _, name := range ModuleBooleanFields {
		value, present := lookup(name)
		if !present {
			continue
		}
		if flag, code := drfBoolean(value, false); code != "" {
			errors[name] = []string{code}
		} else {
			write.Booleans[name] = *flag
		}
	}
	for _, name := range []string{"start_date", "end_date"} {
		value, present := lookup(name)
		if !present {
			continue
		}
		if value == nil {
			write.Dates[name] = nil
		} else if date, ok := ParseSubscriptionMembershipDate(value); ok {
			write.Dates[name] = &date
		} else {
			errors[name] = []string{ModuleDate}
		}
	}
	if value, present := lookup("max_responses"); present {
		// PositiveIntegerField: MinValueValidator(0) and the PostgreSQL range.
		if number, codes := drfInteger(value, true, 0, 2147483647); codes != nil {
			errors["max_responses"] = codes
		} else {
			write.MaxPresent, write.Max = true, number
		}
	}
	for _, name := range []string{"payment_data", "elements"} {
		value, present := object.Lookup(name)
		if !present {
			continue
		}
		if form {
			// JSONField.get_value marks HTML input as a JSON string: json.loads.
			text, _ := value.(string)
			parsed, err := ParseMessageMutationsJSON([]byte(text))
			if err != nil {
				errors[name] = []string{ModuleJSON}
				continue
			}
			value = parsed
		}
		if value == nil {
			errors[name] = []string{PaymentCategoryValidationNull}
			continue
		}
		write.JSON[name] = value
	}
	if len(errors) > 0 {
		return write, errors
	}
	return write, nil
}

// ModuleJSONText is the JSON a field value is stored and rendered with
// (json.dumps; request order kept).
func ModuleJSONText(value any) string { return string(PythonJSONDumps(value)) }

// ModuleDRFCode is the ErrorDetail code of a validation code.
func ModuleDRFCode(code string) string {
	switch {
	case code == PaymentCategoryValidationRequired:
		return "required"
	case code == PaymentCategoryValidationNull:
		return "null"
	case code == PaymentCategoryValidationBlank:
		return "blank"
	case strings.HasPrefix(code, "max:"):
		return "max_length"
	case strings.HasPrefix(code, "unique:"):
		return "unique"
	case strings.HasPrefix(code, InvoicePartyMaxValue):
		return "max_value"
	case strings.HasPrefix(code, "min-value:"):
		return "min_value"
	case code == DRFValidationMaxStringLength:
		return "max_string_length"
	case code == PaymentCategoryValidationDateTimeOverflow:
		return "overflow"
	case code == "null-character":
		return "null_characters_not_allowed"
	case strings.HasPrefix(code, "surrogate:"):
		return "surrogate_characters_not_allowed"
	}
	return "invalid"
}
