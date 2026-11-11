package dto

import (
	"encoding/json"
	"errors"
	"math"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// PersonaSubsWrite is AssociateSubscriptionSerializer's validated data before
// the related-field lookups: present keys only (the read-only subscription_id
// is ignored).
type PersonaSubsWrite struct {
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

// personaSubsFormObject is Field.get_value over an HTML form for every writable
// field, as the JSON object the field validation reads: a key the form lacks is
// False for a boolean and [] for tags unless the update is partial; an empty
// input is None for a nullable field (kept empty when the field may be blank)
// and absent for a field with a default; a JSON field is json.loads of its text
// (a file's name); tags are getlist. rejected is JSON text nested past the
// ported depth (Python's recursion limit depends on the live call stack).
func personaSubsFormObject(form domain.RequestObject, partial bool, invalid map[string][]string) (object domain.RequestObject, rejected bool) {
	set := func(name string, value any) { object = append(object, domain.RequestField{Name: name, Value: value}) }
	plain := func(name string, nullable bool) {
		value, present := form.Lookup(name)
		switch {
		case !present:
		case value == "" && nullable:
			set(name, nil)
		case value == "":
		default:
			set(name, value)
		}
	}
	for _, name := range PersonaSubsJSONFields {
		value, present := form.Lookup(name)
		if !present {
			continue
		}
		if upload, isUpload := value.(domain.RequestUpload); isUpload {
			value = upload.Name
		}
		// A lone surrogate escape stays in the value: the jsonb column rejects it at the save.
		tree, valid, _, deep := automationFormTree(value)
		switch {
		case deep:
			rejected = true
		case !valid:
			invalid[name] = []string{CourseSubscriptionJSONInvalid}
		default:
			set(name, personaSubsFormJSON{tree})
		}
	}
	for _, name := range []string{"type", "role", "status_flag"} {
		plain(name, false)
	}
	for _, name := range PersonaSubsDateFields {
		plain(name, true)
	}
	plain("creation_date", false)
	for _, field := range personaSubsTextFields {
		if value, present := form.Lookup(field.Name); present {
			set(field.Name, value) // modelTextValue applies the empty-input rule
		}
	}
	for _, name := range PersonaSubsBooleanFields {
		if _, present := form.Lookup(name); !present && !partial {
			set(name, false)
			continue
		}
		plain(name, false)
	}
	for _, name := range PersonaSubsRelationFields {
		plain(name, true) // RelatedField.run_validation: '' is None
	}
	if values, present := form.List("tags"); present {
		set("tags", append([]any{}, values...))
	} else if !partial {
		set("tags", []any{})
	}
	return object, rejected
}

// personaSubsFormJSON wraps a JSON field's parsed form value so that a JSON
// null is the value None, not an absent field.
type personaSubsFormJSON struct{ value any }

// personaSubsJSONFinite reports a parsed value without a float beyond the
// double range (Python's inf).
func personaSubsJSONFinite(value any) bool {
	switch typed := value.(type) {
	case json.Number:
		if strings.ContainsAny(string(typed), ".eE") {
			number, _ := strconv.ParseFloat(string(typed), 64)
			return !math.IsInf(number, 0)
		}
	case domain.RequestObject:
		for _, field := range typed {
			if !personaSubsJSONFinite(field.Value) {
				return false
			}
		}
	case []any:
		for _, item := range typed {
			if !personaSubsJSONFinite(item) {
				return false
			}
		}
	}
	return true
}

// ErrPersonaSubsFormJSON is form JSON text nested past the ported depth.
var ErrPersonaSubsFormJSON = errors.New("personas-subscriptions form JSON text nested past the ported depth")

// ParsePersonaSubsWrite ports the serializer's field validation (JSON and
// form bodies). invalid holds the field codes; err is a value the port does
// not reproduce (strptime with Unicode digits, form JSON text nested past the ported depth).
func ParsePersonaSubsWrite(body any, partial, form bool) (PersonaSubsWrite, map[string][]string, error) {
	invalid := map[string][]string{}
	object, ok := body.(domain.RequestObject)
	if !ok {
		invalid["non_field_errors"] = []string{"dictionary:" + pythonTypeName(body)}
		return PersonaSubsWrite{}, invalid, nil
	}
	if form {
		rejected := false
		if object, rejected = personaSubsFormObject(object, partial, invalid); rejected {
			return PersonaSubsWrite{}, nil, ErrPersonaSubsFormJSON
		}
	}
	write := PersonaSubsWrite{JSON: map[string]any{}, Choices: map[string]int64{}, Dates: map[string]*time.Time{}, Text: map[string]*string{},
		Booleans: map[string]bool{}, Relations: map[string]*string{}, RelationInput: map[string]any{}}
	for _, name := range PersonaSubsJSONFields {
		if value, present := object.Lookup(name); present {
			if parsed, isForm := value.(personaSubsFormJSON); isForm {
				value = parsed.value
			} else if !personaSubsJSONFinite(value) {
				// JSONField.to_internal_value: json.dumps(allow_nan=False) of a parsed body.
				invalid[name] = []string{CourseSubscriptionJSONInvalid}
				continue
			}
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
			if text, codes := modelTextValue(field, value, form); len(codes) > 0 {
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
