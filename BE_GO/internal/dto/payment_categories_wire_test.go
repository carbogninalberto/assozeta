package dto

import (
	"encoding/json"
	"errors"
	"reflect"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestPaymentCategoryWireValues(t *testing.T) {
	stripped, stripErr := ParsePaymentCategoryWrite(domain.RequestObject{{Name: "name", Value: "\u001cx\u001f"}}, true)
	if stripErr != nil || stripped.Name == nil || *stripped.Name != "x" {
		t.Errorf("Python CharField whitespace: %#v %v", stripped.Name, stripErr)
	}
	for _, body := range []any{nil, []any{}} {
		input, err := ParsePaymentCategoryWrite(body, true)
		if err == nil || len(input.ValidationErrors) == 0 {
			t.Errorf("non-object PATCH must retain validation failure for service: %#v %v", input, err)
		}
	}
	for _, raw := range []string{"2021-01-02T03:04:05.120000", "2021-01-02 03:04:05.120000+00:00"} {
		value, err := ParsePaymentCategoryWrite(domain.RequestObject{{Name: "creation_date", Value: raw}}, true)
		if err != nil || value.CreationDate == nil || paymentCategoryResponseTime(*value.CreationDate) != "2021-01-02T03:04:05.120000Z" {
			t.Errorf("Django-compatible datetime %q: %#v %v", raw, value, err)
		}
	}
	for raw, expected := range map[string]string{"123": "123", "1.20": "1.2", "1e20": "1e+20", "1.0": "1.0"} {
		value, err := ParsePaymentCategoryWrite(domain.RequestObject{{Name: "name", Value: json.Number(raw)}}, true)
		if err != nil || value.Name == nil || *value.Name != expected {
			t.Errorf("numeric name %s: %#v %v", raw, value.Name, err)
		}
	}
	for _, field := range []string{"payment_category_id", "name", "type", "expense", "tax_deductible", "deleted", "archived"} {
		_, err := ParsePaymentCategoryWrite(domain.RequestObject{{Name: field, Value: nil}}, true)
		var validation *PaymentCategoryValidationError
		if !errors.As(err, &validation) || !reflect.DeepEqual(validation.Errors, map[string]any{field: []string{PaymentCategoryValidationNull}}) {
			t.Errorf("null %s: %#v", field, err)
		}
	}
	_, err := ParsePaymentCategoryWrite(domain.RequestObject{{Name: "vat_management", Value: []any{}}}, true)
	var validation *PaymentCategoryValidationError
	want := map[string]any{"vat_management": map[string]any{"non_field_errors": []string{"dictionary:list"}}}
	if !errors.As(err, &validation) || !reflect.DeepEqual(validation.Errors, want) {
		t.Errorf("nested VAT list error: %#v, want %#v", validation, want)
	}
}

func TestPaymentCategoryNameCodeUnits(t *testing.T) {
	for raw, expected := range map[string][]string{
		`{"name":"x\u0000y"}`:                  {"null-character"},
		`{"name":"x\ud800y"}`:                  {"surrogate:D800"},
		`{"name":"x\udc00y"}`:                  {"surrogate:DC00"},
		`{"name":"x\u0000\ud800\udc01\udfff"}`: {"null-character", "surrogate:DFFF"},
		`{"name":"x\ud800\udc00y"}`:            nil,
		`{"name":"x\ufffdy"}`:                  nil,
		`{"name":"Valid","ignored":"\ud800"}`:  nil,
	} {
		body, err := ParseProfileTablesJSON([]byte(raw))
		if err != nil {
			t.Fatal(err)
		}
		_, err = ParsePaymentCategoryWrite(body, true)
		if expected == nil {
			if err != nil {
				t.Errorf("valid/ignored code units %s: %v", raw, err)
			}
			continue
		}
		var validation *PaymentCategoryValidationError
		if !errors.As(err, &validation) || !reflect.DeepEqual(validation.Errors["name"], expected) {
			t.Errorf("%s: %#v, want %#v", raw, validation, expected)
		}
	}
}

func TestPaymentCategoryDateOverflowValidation(t *testing.T) {
	for _, value := range []string{"0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"} {
		input, err := ParsePaymentCategoryWrite(domain.RequestObject{{Name: "creation_date", Value: value}}, true)
		if err == nil || !reflect.DeepEqual(input.ValidationErrors["creation_date"], []string{"datetime-overflow"}) {
			t.Errorf("%q: %#v %v", value, input, err)
		}
	}
}
