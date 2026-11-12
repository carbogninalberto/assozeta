package handler

import (
	"net/http"
	"reflect"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// TestInvoicePartyMessagesNestedPayment renders the codes the nested payment
// validator emits: the date format and the untranslated address_cap message
// are the invoice-party table's own, the rest come from the shared table.
func TestInvoicePartyMessagesNestedPayment(t *testing.T) {
	const nestedPaymentMissing = "a1000000-0000-4000-8000-0000009fffff"
	request := func(language string) *http.Request {
		header := http.Header{}
		header.Set("Accept-Language", language)
		return &http.Request{Header: header}
	}
	invalid := map[string]any{"payment": map[string]any{
		"user":              map[string]any{"born_date": []string{dto.PersonaDateFormat}, "role": []string{"choice:9"}},
		"associate":         map[string]any{"address_cap": []string{dto.AssociateCapMessage}, "is_minor": []string{}},
		"instructor":        []string{dto.SupplierValidationEmail},
		"invoice":           map[string]any{"meta": []string{dto.CourseSubscriptionJSONInvalid}},
		"documents":         []string{"not-a-list:str"},
		"payment_id":        []string{"unique:payment:payment id"},
		"amount":            []string{dto.DRFValidationNumber},
		"archived":          []string{"dictionary:list"},
		"description":       []string{"max-whole-digits:2"},
		"payment_intent_id": []string{"max:125"},
		"group":             []string{"related-does-not-exist:" + nestedPaymentMissing},
		"signature":         []string{"related-invalid-uuid:abc"},
		"number":            []string{dto.InvoicePartyMaxValue + "2147483647"},
	}}
	want := map[string]any{"payment": map[string]any{
		"user": map[string]any{"born_date": []string{"Date has wrong format. Use one of these formats instead: DD/MM/YYYY."},
			"role": []string{`"9" is not a valid choice.`}},
		"associate":         map[string]any{"address_cap": []string{"Enter exactly 5 digits"}, "is_minor": []string{}},
		"instructor":        []string{"Enter a valid email address."},
		"invoice":           map[string]any{"meta": []string{"Value must be valid JSON."}},
		"documents":         []string{`Expected a list of items but got type "str".`},
		"payment_id":        []string{"payment with this payment id already exists."},
		"amount":            []string{"A valid number is required."},
		"archived":          []string{"Invalid data. Expected a dictionary, but got list."},
		"description":       []string{"Ensure that there are no more than 2 digits before the decimal point."},
		"payment_intent_id": []string{"Ensure this field has no more than 125 characters."},
		"group":             []string{`Invalid pk "` + nestedPaymentMissing + `" - object does not exist.`},
		"signature":         []string{"“abc” is not a valid UUID."},
		"number":            []string{"Ensure this value is less than or equal to 2147483647."},
	}}
	if got := invoicePartyMessages(request("en"), invalid); !reflect.DeepEqual(got, want) {
		t.Fatalf("english messages %#v, want %#v", got, want)
	}
	italian := invoicePartyMessages(request("it"), map[string]any{"payment": map[string]any{
		"born_date": []string{dto.PersonaDateFormat}, "address_cap": []string{dto.AssociateCapMessage}}})
	wantItalian := map[string]any{"payment": map[string]any{
		"born_date":   []string{"La data è in un formato errato. Usa uno dei seguenti formati: DD/MM/YYYY."},
		"address_cap": []string{"Enter exactly 5 digits"}}}
	if !reflect.DeepEqual(italian, wantItalian) {
		t.Fatalf("italian messages %#v, want %#v", italian, wantItalian)
	}
}
