package dto

import (
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// PersonaSubsWrite is AssociateSubscriptionSerializer's validated data before
// the related-field lookups: present keys only.
type PersonaSubsWrite struct {
	ID       *string
	JSON     map[string]any        // custom_data, additional_fields, meta (nil is None)
	Choices  map[string]int64      // type, role, status_flag
	Dates    map[string]*time.Time // acceptance_date, resignation_date, start_date, end_date (nil is None)
	Created  *time.Time
	Text     map[string]*string // signature_url, subscription_number, subscription_type, notes (nil is None)
	Booleans map[string]bool
	// Relations are the syntactically valid primary keys (nil is None);
	// RelationInput keeps the submitted value for the does-not-exist message.
	Relations     map[string]*string
	RelationInput map[string]any
	TagsPresent   bool
	Tags          []any
}

// PersonaSubsJSONFields, PersonaSubsDateFields, PersonaSubsBooleanFields and
// PersonaSubsRelationFields are the writable model fields by kind, each in
// serializer field order.
var (
	PersonaSubsJSONFields     = []string{"custom_data", "additional_fields", "meta"}
	PersonaSubsDateFields     = []string{"acceptance_date", "resignation_date", "start_date", "end_date"}
	PersonaSubsBooleanFields  = []string{"archived", "draft", "trial", "deleted", "competitive"}
	PersonaSubsRelationFields = []string{"sport_association", "supplier", "instructor", "user", "document_pdf", "payment", "group"}
	personaSubsChoices        = map[string]int64{"type": 3, "role": 8, "status_flag": 5}
	personaSubsTextFields     = []SupplierField{
		{Name: "signature_url", Max: 1024, Null: true, Blank: true},
		{Name: "subscription_number", Null: true},
		{Name: "subscription_type", Max: 255, Null: true},
		{Name: "notes", Null: true, Blank: true},
	}
	personaSubsRequired = map[string]bool{"sport_association": true, "user": true}
)

// personaSubsChoice is ChoiceField.to_internal_value over choices 1..maximum:
// str(data) must be one of the choice keys' str().
func personaSubsChoice(value any, maximum int64) (int64, string) {
	if value == nil {
		return 0, PaymentCategoryValidationNull
	}
	key := PythonStr(value)
	if number, err := strconv.ParseInt(key, 10, 64); err == nil && strconv.FormatInt(number, 10) == key && number >= 1 && number <= maximum {
		return number, ""
	}
	return 0, "choice:" + key
}

// ParsePersonaSubsWrite ports the serializer's field validation (JSON
// bodies). invalid holds the field codes; err is a value the port does not
// reproduce (strptime with Unicode digits).
func ParsePersonaSubsWrite(body any, partial bool) (PersonaSubsWrite, map[string][]string, error) {
	invalid := map[string][]string{}
	object, ok := body.(domain.RequestObject)
	if !ok {
		invalid["non_field_errors"] = []string{"dictionary:" + pythonTypeName(body)}
		return PersonaSubsWrite{}, invalid, nil
	}
	write := PersonaSubsWrite{JSON: map[string]any{}, Choices: map[string]int64{}, Dates: map[string]*time.Time{}, Text: map[string]*string{},
		Booleans: map[string]bool{}, Relations: map[string]*string{}, RelationInput: map[string]any{}}
	if value, present := object.Lookup("subscription_id"); present {
		if value == nil {
			invalid["subscription_id"] = []string{PaymentCategoryValidationNull}
		} else if id, ok := personaDRFUUID(value); ok {
			write.ID = &id
		} else {
			invalid["subscription_id"] = []string{PaymentCategoryValidationUUID}
		}
	}
	for _, name := range PersonaSubsJSONFields {
		if value, present := object.Lookup(name); present {
			write.JSON[name] = value
		}
	}
	for _, name := range []string{"type", "role", "status_flag"} {
		if value, present := object.Lookup(name); present {
			if number, code := personaSubsChoice(value, personaSubsChoices[name]); code != "" {
				invalid[name] = []string{code}
			} else {
				write.Choices[name] = number
			}
		}
	}
	for _, name := range PersonaSubsDateFields {
		value, present := object.Lookup(name)
		if !present {
			continue
		}
		if value == nil {
			write.Dates[name] = nil
			continue
		}
		parsed, ok, err := ParseDMYDate(value)
		if err != nil {
			return write, nil, err
		}
		if !ok {
			invalid[name] = []string{PersonaDateFormat}
			continue
		}
		write.Dates[name] = &parsed
	}
	if value, present := object.Lookup("creation_date"); present {
		if value == nil {
			invalid["creation_date"] = []string{PaymentCategoryValidationNull}
		} else if parsed, code := courseSubscriptionDateTime(value); code != "" {
			invalid["creation_date"] = []string{code}
		} else {
			write.Created = parsed
		}
	}
	for _, field := range personaSubsTextFields {
		if value, present := object.Lookup(field.Name); present {
			if text, codes := modelTextValue(field, value, false); len(codes) > 0 {
				invalid[field.Name] = codes
			} else {
				write.Text[field.Name] = text
			}
		}
	}
	for _, name := range PersonaSubsBooleanFields {
		if value, present := object.Lookup(name); present {
			if flag, code := drfBoolean(value, false); code != "" {
				invalid[name] = []string{code}
			} else {
				write.Booleans[name] = *flag
			}
		}
	}
	for _, name := range PersonaSubsRelationFields {
		value, present := object.Lookup(name)
		if !present {
			if personaSubsRequired[name] && !partial {
				invalid[name] = []string{PaymentCategoryValidationRequired}
			}
			continue
		}
		if value == nil {
			if personaSubsRequired[name] {
				invalid[name] = []string{PaymentCategoryValidationNull}
			} else {
				write.Relations[name] = nil
			}
			continue
		}
		id, code := supplierRelated(value)
		if code != "" {
			invalid[name] = []string{code}
			continue
		}
		write.Relations[name], write.RelationInput[name] = &id, value
	}
	if value, present := object.Lookup("tags"); present {
		// ManyRelatedField: a str or a value without __iter__ is not a list;
		// a dict iterates its keys.
		switch typed := value.(type) {
		case nil:
			invalid["tags"] = []string{PaymentCategoryValidationNull}
		case []any:
			write.TagsPresent, write.Tags = true, typed
		case domain.RequestObject:
			write.TagsPresent, write.Tags = true, make([]any, 0, len(typed))
			for _, field := range typed {
				write.Tags = append(write.Tags, field.Name)
			}
		default:
			invalid["tags"] = []string{"not-a-list:" + pythonTypeName(value)}
		}
	}
	return write, invalid, nil
}

// PersonaSubsRelated is a tags item's PrimaryKeyRelatedField syntax check.
func PersonaSubsRelated(value any) (string, string) { return supplierRelated(value) }
