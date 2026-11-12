package dto

import (
	"errors"
	"reflect"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestProfileUpdateRequiredPersonFields(t *testing.T) {
	fields := domain.RequestObject{{Name: "first_name", Value: "Next"}, {Name: "last_name"}, {Name: "username", Value: "straße"}, {Name: "avatar_image"}, {Name: "sport_association", Value: "ignored for athletes"}}
	wrap := func(value any) domain.RequestObject { return domain.RequestObject{{Name: "user_data", Value: value}} }
	got, err := ParseProfileUpdate(wrap(fields))
	if err != nil || !reflect.DeepEqual(got.Person, map[string]any{"first_name": "Next", "username": "straße"}) || got.Association != "ignored for athletes" {
		t.Fatal(got, err)
	}
	for i := range fields {
		missing := append(append(domain.RequestObject{}, fields[:i]...), fields[i+1:]...)
		if _, err := ParseProfileUpdate(wrap(missing)); !errors.Is(err, ErrProfileUpdateShape) {
			t.Fatalf("accepted missing %s: %v", fields[i].Name, err)
		}
	}
	for _, body := range []any{nil, []any{}, "text", domain.RequestObject{}, wrap(nil), wrap("text")} {
		if _, err := ParseProfileUpdate(body); !errors.Is(err, ErrProfileUpdateShape) {
			t.Fatalf("accepted malformed body: %v", err)
		}
	}
	fields = append(fields, domain.RequestField{Name: "user_id", Value: "foreign"}, domain.RequestField{Name: "email", Value: "ignored@example.test"})
	got, err = ParseProfileUpdate(wrap(fields))
	if err != nil || len(got.Person) != 2 {
		t.Fatal("unlisted person field admitted", got, err)
	}
}

func TestProfileUpdateAssociationNullAndPresenceRules(t *testing.T) {
	// This list is an authored contract, not a copy of the parser's field list.
	required := []string{"denomination", "tax_code", "address", "address_cap", "address_city", "enable_quotes_management", "configuration", "federation", "enroll_number", "sport", "president_first_name", "president_last_name", "stripe_available_methods", "invoice_template", "subscription_template", "extra_text_invoices", "checkout_info"}
	input := domain.RequestObject{}
	for _, name := range required {
		input = append(input, domain.RequestField{Name: name})
	}
	got, err := ParseProfileAssociationUpdate(input)
	if err != nil || !reflect.DeepEqual(got, map[string]any{"checkout_info": nil}) {
		t.Fatal("required-null policy", got, err)
	}
	for i := range input {
		missing := append(append(domain.RequestObject{}, input[:i]...), input[i+1:]...)
		if _, err := ParseProfileAssociationUpdate(missing); !errors.Is(err, ErrProfileUpdateShape) {
			t.Fatalf("accepted missing %s: %v", input[i].Name, err)
		}
	}
	for _, name := range []string{"document_header", "invoice_footer", "president_signature", "stamp", "iban", "abbreviated", "vat_number", "website", "whatsapp"} {
		input = append(input, domain.RequestField{Name: name})
	}
	input = append(input, domain.RequestField{Name: "sport_association_id", Value: "foreign"})
	got, err = ParseProfileAssociationUpdate(input)
	want := map[string]any{"checkout_info": nil, "document_header": nil, "invoice_footer": nil, "president_signature": nil, "stamp": nil}
	if err != nil || !reflect.DeepEqual(got, want) {
		t.Fatal("optional-null policy", got, err)
	}
	for i := range input {
		if input[i].Name == "enable_quotes_management" {
			input[i].Value = false
		}
	}
	got, err = ParseProfileAssociationUpdate(input)
	if err != nil || got["enable_quotes_management"] != false {
		t.Fatal("false was treated as null", got, err)
	}
	for _, value := range []any{nil, "text", []any{}, true} {
		if _, err := ParseProfileAssociationUpdate(value); !errors.Is(err, ErrProfileUpdateShape) {
			t.Fatal("accepted malformed association", err)
		}
	}
}
