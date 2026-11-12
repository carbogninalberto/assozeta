package dto

import (
	"reflect"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// invoicePartyJSONBody is a reference JSON body parsed as the request parser does.
func invoicePartyJSONBody(t *testing.T, raw string) domain.RequestObject {
	t.Helper()
	value, err := ParseReferenceJSON([]byte(raw))
	if err != nil {
		t.Fatalf("parse %s: %v", raw, err)
	}
	return value.(domain.RequestObject)
}

func invoicePartyPointer(value string) *string { return &value }

func TestParseSupplierInvoiceNestedSupplierJSONID(t *testing.T) {
	const canonical = "12345678-1234-5678-9abc-def012345678"
	for _, test := range []struct {
		name  string
		value string
		id    *string
		codes []string
	}{
		{name: "malformed", value: `"abc"`, codes: []string{PaymentCategoryValidationUUID}},
		{name: "blank", value: `""`, codes: []string{PaymentCategoryValidationUUID}},
		{name: "null", value: `null`, codes: []string{PaymentCategoryValidationNull}},
		{name: "integer", value: `5`, id: invoicePartyPointer("00000000-0000-0000-0000-000000000005")},
		{name: "boolean", value: `true`, id: invoicePartyPointer("00000000-0000-0000-0000-000000000001")},
		{name: "uuid", value: `"` + canonical + `"`, id: invoicePartyPointer(canonical)},
	} {
		t.Run(test.name, func(t *testing.T) {
			body := invoicePartyJSONBody(t, `{"supplier":{"name":"N","supplier_id":`+test.value+`}}`)
			input, _ := ParseSupplierInvoice(body, false, false)
			if !input.NestedSupplierSet || input.NestedSupplierForm {
				t.Fatalf("nested supplier not read as a JSON object: %#v", input)
			}
			if !reflect.DeepEqual(input.NestedSupplierIDCodes, test.codes) {
				t.Fatalf("codes %v, want %v", input.NestedSupplierIDCodes, test.codes)
			}
			if !reflect.DeepEqual(input.NestedSupplierID, test.id) {
				t.Fatalf("id %v, want %v", input.NestedSupplierID, test.id)
			}
			if input.NotPorted {
				t.Fatal("a nested supplier_id is ported")
			}
		})
	}
}

func TestParseSupplierInvoiceNestedSupplierJSONWithoutID(t *testing.T) {
	input, _ := ParseSupplierInvoice(invoicePartyJSONBody(t, `{"supplier":{"name":"N"}}`), false, false)
	if !input.NestedSupplierSet || input.NestedSupplierID != nil || len(input.NestedSupplierIDCodes) > 0 {
		t.Fatalf("unexpected nested supplier id: %#v", input)
	}
	input, _ = ParseSupplierInvoice(invoicePartyJSONBody(t, `{"supplier":{}}`), true, false)
	if !input.NestedSupplierSet || input.NestedSupplierID != nil || len(input.NestedSupplierIDCodes) > 0 {
		t.Fatalf("unexpected nested supplier id: %#v", input)
	}
}

func TestParseSupplierInvoiceNestedSupplierForm(t *testing.T) {
	const canonical = "12345678-1234-5678-9abc-def012345678"
	object := instanceAdminFormBody(t, "supplier.name=First&supplier.supplier_id="+canonical+"&supplier.name=Last&notes=n")
	input, _ := ParseSupplierInvoice(object, true, true)
	if !input.NestedSupplierSet || !input.NestedSupplierForm {
		t.Fatalf("nested supplier form not read: %#v", input)
	}
	if name, _ := input.NestedSupplier.Lookup("name"); name != "Last" {
		t.Fatalf("a repeated key keeps its last value: %v", name)
	}
	if input.NestedSupplierID == nil || *input.NestedSupplierID != canonical || len(input.NestedSupplierIDCodes) > 0 {
		t.Fatalf("supplier_id %v %v", input.NestedSupplierID, input.NestedSupplierIDCodes)
	}
	if _, present := input.NestedSupplier.Lookup("deleted"); present {
		t.Fatal("a partial nested form keeps an absent boolean absent")
	}
	// A full nested form's absent BooleanField is False.
	full, _ := ParseSupplierInvoice(instanceAdminFormBody(t, "supplier.name=Last"), false, true)
	deleted, present := full.NestedSupplier.Lookup("deleted")
	if !present || deleted != false {
		t.Fatalf("full nested form deleted %v %v", deleted, present)
	}
	// A blank BooleanField of a nested form is absent; a full one adds its default.
	blankDeleted, _ := ParseSupplierInvoice(instanceAdminFormBody(t, "supplier.deleted=&supplier.email="), true, true)
	if _, present := blankDeleted.NestedSupplier.Lookup("deleted"); present {
		t.Fatal("a blank deleted field of a partial nested form is absent")
	}
	fullDeleted, _ := ParseSupplierInvoice(instanceAdminFormBody(t, "supplier.deleted=&supplier.name=Last"), false, true)
	if deleted, present := fullDeleted.NestedSupplier.Lookup("deleted"); !present || deleted != false {
		t.Fatalf("full nested form deleted %v %v", deleted, present)
	}
	// '' on the nested UUIDField is absent.
	blank, _ := ParseSupplierInvoice(instanceAdminFormBody(t, "supplier.name=Last&supplier.supplier_id="), true, true)
	if blank.NestedSupplierID != nil || len(blank.NestedSupplierIDCodes) > 0 {
		t.Fatalf("blank form supplier_id %v %v", blank.NestedSupplierID, blank.NestedSupplierIDCodes)
	}
	malformed, _ := ParseSupplierInvoice(instanceAdminFormBody(t, "supplier.supplier_id=abc"), true, true)
	if malformed.NestedSupplierID != nil || !reflect.DeepEqual(malformed.NestedSupplierIDCodes, []string{PaymentCategoryValidationUUID}) {
		t.Fatalf("malformed form supplier_id %v %v", malformed.NestedSupplierID, malformed.NestedSupplierIDCodes)
	}
	// A file part stands in for the nested text field.
	upload := instanceAdminUpload("supplier.name", "n.txt")
	withFile, _ := ParseSupplierInvoice(instanceAdminFormBody(t, "", upload), false, true)
	if name, _ := withFile.NestedSupplier.Lookup("name"); name != (domain.RequestUpload{Name: "n.txt", Whole: true}) {
		t.Fatalf("nested file part %v", name)
	}
	// No supplier.<key> field means no nested value; the payment.<key> fields
	// form the nested payment value instead.
	absent, _ := ParseSupplierInvoice(instanceAdminFormBody(t, "notes=n"), false, true)
	if absent.NestedSupplierSet || absent.NestedPaymentSet || absent.NotPorted {
		t.Fatalf("absent nested supplier %#v", absent)
	}
	payment, _ := ParseSupplierInvoice(instanceAdminFormBody(t, "payment.notes=n"), false, true)
	if payment.NestedSupplierSet || !payment.NestedPaymentSet || !payment.NestedPaymentForm || payment.NotPorted {
		t.Fatalf("nested payment form %#v", payment)
	}
}

func TestParseSupplierInvoiceNestedPaymentJSON(t *testing.T) {
	input, errors := ParseSupplierInvoice(invoicePartyJSONBody(t, `{"payment":{"amount":"3"}}`), true, false)
	if !input.NestedPaymentSet || input.NestedPaymentForm || input.NotPorted || input.PaymentNull {
		t.Fatalf("nested payment object %#v", input)
	}
	nested, ok := input.NestedPayment.(domain.RequestObject)
	if !ok {
		t.Fatalf("nested payment value %T", input.NestedPayment)
	}
	if amount, present := nested.Lookup("amount"); !present || amount != "3" {
		t.Fatalf("nested payment amount %v %v", amount, present)
	}
	if len(errors) > 0 {
		t.Fatalf("errors %#v", errors)
	}
	null, errors := ParseSupplierInvoice(invoicePartyJSONBody(t, `{"payment":null}`), true, false)
	if null.NestedPaymentSet || !null.PaymentNull || len(errors) > 0 {
		t.Fatalf("null nested payment %#v %#v", null, errors)
	}
	for _, test := range []struct{ body, kind string }{
		{body: `{"payment":[]}`, kind: "list"},
		{body: `{"payment":"x"}`, kind: "str"},
		{body: `{"payment":5}`, kind: "int"},
	} {
		invalid, errors := ParseSupplierInvoice(invoicePartyJSONBody(t, test.body), true, false)
		want := map[string]any{"non_field_errors": []string{"dictionary:" + test.kind}}
		if invalid.NestedPaymentSet || !reflect.DeepEqual(errors["payment"], want) {
			t.Fatalf("%s: nested payment %#v errors %#v", test.body, invalid, errors)
		}
	}
}

func TestParseSupplierInvoiceNestedPaymentForm(t *testing.T) {
	input, errors := ParseSupplierInvoice(instanceAdminFormBody(t,
		"payment.amount=x&payment.user.email=y&payment.instructor.documents=abc&payment.instructor.documents=doc&notes=n"), true, true)
	if !input.NestedPaymentSet || !input.NestedPaymentForm || input.NotPorted || len(errors) > 0 {
		t.Fatalf("nested payment form %#v %#v", input, errors)
	}
	nested, ok := input.NestedPayment.(domain.RequestObject)
	if !ok {
		t.Fatalf("nested payment value %T", input.NestedPayment)
	}
	if amount, present := nested.Lookup("amount"); !present || amount != "x" {
		t.Fatalf("nested payment amount %v %v", amount, present)
	}
	if _, present := nested.Lookup("payment.amount"); present {
		t.Fatal("the payment. prefix is removed")
	}
	if email, present := nested.Lookup("user.email"); !present || email != "y" {
		t.Fatalf("nested user email %v %v", email, present)
	}
	// The repeated field keeps every value: the validator reads the first
	// failing item of a many field from getlist.
	documents, present := nested.Lookup("instructor.documents")
	if !present || documents != "doc" {
		t.Fatalf("nested documents %v %v", documents, present)
	}
	for _, field := range nested {
		if field.Name == "instructor.documents" && !reflect.DeepEqual(field.All, []any{"abc", "doc"}) {
			t.Fatalf("repeated values %#v", field.All)
		}
	}
	// The built value feeds the validator as DRF's HTML-form input does.
	invalid, notPorted, err := ValidateNestedPayment(nested, true, true, NestedPaymentLookups{})
	if err != nil || notPorted {
		t.Fatalf("validate %v %v", notPorted, err)
	}
	want := map[string]any{"user": map[string]any{"email": []string{SupplierValidationEmail}},
		"amount": []string{DRFValidationNumber}, "instructor": map[string]any{"documents": []string{"related-invalid-uuid:abc"}}}
	if !reflect.DeepEqual(invalid, want) {
		t.Fatalf("validator errors %#v, want %#v", invalid, want)
	}
	absent, _ := ParseSupplierInvoice(instanceAdminFormBody(t, "notes=n&payment="), false, true)
	if absent.NestedPaymentSet || absent.NestedPaymentForm {
		t.Fatalf("no payment.<key> field means no nested payment %#v", absent)
	}
	valid, errors := ParseSupplierInvoice(instanceAdminFormBody(t, "payment.amount=3"), true, true)
	if len(errors) > 0 {
		t.Fatalf("errors %#v", errors)
	}
	value, _ := valid.NestedPayment.(domain.RequestObject)
	if errs, notPorted, err := ValidateNestedPayment(value, true, true, NestedPaymentLookups{}); err != nil || notPorted || errs != nil {
		t.Fatalf("valid nested payment %#v %v %v", errs, notPorted, err)
	}
}
