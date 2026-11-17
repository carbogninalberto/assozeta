package dto

import (
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// personaRelatedFound is the serializer's related lookup for an id that exists.
func personaRelatedFound(string, string) (bool, error) { return true, nil }

// Field.get_value on an HTML input: a present empty value on a boolean that is
// not required and not nullable is empty, so the field is skipped and the model
// default (a create) or the stored value (a partial update) stays. An absent key
// on a non-partial form create takes BooleanField.default_empty_html False.
func TestPersonaFormEmptyBooleanSkipped(t *testing.T) {
	for _, options := range []PersonaFieldOptions{{Form: true}, {Form: true, Partial: true}} {
		body := domain.RequestObject{}
		for _, name := range PersonaBooleanFields {
			body = append(body, domain.RequestField{Name: name, Value: ""})
		}
		fields, invalid, err := ParsePersonaFields(body, options, personaRelatedFound)
		if err != nil || invalid != nil {
			t.Fatalf("%+v: %v %v", options, invalid, err)
		}
		for _, name := range PersonaBooleanFields {
			if _, present := fields.Booleans[name]; present {
				t.Errorf("%+v: empty input %s in the validated data", options, name)
			}
		}
	}
	fields, invalid, err := ParsePersonaFields(domain.RequestObject{}, PersonaFieldOptions{Form: true}, personaRelatedFound)
	if err != nil || invalid != nil {
		t.Fatalf("absent keys: %v %v", invalid, err)
	}
	for _, name := range PersonaBooleanFields {
		if value, present := fields.Booleans[name]; !present || value {
			t.Errorf("absent key %s: %v %v", name, value, present)
		}
	}
	// A partial form update skips an absent key too: the stored value stays.
	fields, invalid, err = ParsePersonaFields(domain.RequestObject{}, PersonaFieldOptions{Form: true, Partial: true}, personaRelatedFound)
	if err != nil || invalid != nil || len(fields.Booleans) != 0 {
		t.Fatalf("partial absent keys: %#v %v %v", fields.Booleans, invalid, err)
	}
	// A JSON body's '' is not an HTML empty input: BooleanField.to_internal_value
	// refuses it.
	_, invalid, err = ParsePersonaFields(domain.RequestObject{{Name: "is_minor", Value: ""}},
		PersonaFieldOptions{}, personaRelatedFound)
	if err != nil || invalid == nil || len(invalid.Fields["is_minor"]) != 1 ||
		invalid.Fields["is_minor"][0] != PaymentCategoryValidationBoolean {
		t.Fatalf("JSON empty boolean: %#v %v", invalid, err)
	}
}
