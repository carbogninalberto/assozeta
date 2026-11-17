package dto

import (
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// personaRelatedFound is the serializer's related lookup for an id that exists.
func personaRelatedFound(string, string) (bool, error) { return true, nil }

// parse_html_list matches keys with re.match(r'^tutors_data\[([0-9]+)\](.*)$',
// key): '.' does not match '\n' and '$' matches at the end or just before one
// final '\n', so the text after ']' must be newline-free or newline-free plus
// exactly one final '\n'.
func TestPersonaListFieldPresentIndexedKey(t *testing.T) {
	for _, tc := range []struct {
		name string
		want bool
	}{
		{"tutors_data[0]", true},
		{"tutors_data[0]\n", true},
		{"tutors_data[0]x\n", true},
		{"tutors_data[01]x", true},
		{"tutors_data[0]a\nb", false},
		{"tutors_data[0]\n\n", false},
		{"tutors_data[0]x\r", true},
	} {
		body := domain.RequestObject{{Name: tc.name, Value: ""}}
		if got := PersonaListFieldPresent(body, "tutors_data", true); got != tc.want {
			t.Errorf("%q: got %v, want %v", tc.name, got, tc.want)
		}
	}
	// A JSON body reads the key itself, never the indexed keys.
	if PersonaListFieldPresent(domain.RequestObject{{Name: "tutors_data[0]", Value: ""}}, "tutors_data", false) {
		t.Error("indexed key read outside a form")
	}
}

// An HTML form's ListField reads getlist, so a file part named tutors_data is a
// list holding the file and validates like a string value.
func TestPersonaFieldsTutorsDataUpload(t *testing.T) {
	body := domain.RequestObject{{Name: "tutors_data", Value: domain.RequestUpload{Name: "tutors.csv", Size: 3}}}
	_, invalid, err := ParsePersonaFields(body, PersonaFieldOptions{Form: true, TutorsData: true}, personaRelatedFound)
	if err != nil || invalid != nil {
		t.Fatalf("form file part: %#v %v", invalid, err)
	}
	_, invalid, err = ParsePersonaFields(body, PersonaFieldOptions{TutorsData: true}, personaRelatedFound)
	if err != nil || invalid == nil || len(invalid.Fields["tutors_data"]) != 1 ||
		invalid.Fields["tutors_data"][0] != "not-a-list:InMemoryUploadedFile" {
		t.Fatalf("JSON file part: %#v %v", invalid, err)
	}
}

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
