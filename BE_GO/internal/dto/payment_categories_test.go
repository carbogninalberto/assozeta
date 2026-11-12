package dto

import (
	"encoding/json"
	"errors"
	"reflect"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestPaymentCategoryCreateDefaultsAndNestedVAT(t *testing.T) {
	body := domain.RequestObject{
		{Name: "name", Value: "  Quote associative  "},
		{Name: "expense", Value: "false"},
		{Name: "tax_deductible", Value: true},
		{Name: "type", Value: json.Number("2")},
		{Name: "vat_management", Value: domain.RequestObject{
			{Name: "vat", Value: json.Number("16")},
		}},
	}
	got, err := ParsePaymentCategoryWrite(body, false)
	if err != nil {
		t.Fatal(err)
	}
	if got.Name == nil || *got.Name != "Quote associative" || got.Expense == nil || *got.Expense ||
		got.TaxDeductible == nil || !*got.TaxDeductible || got.Type == nil || *got.Type != 2 {
		t.Fatalf("unexpected category write: %#v", got)
	}
	if !got.VAT.Present || got.VAT.Null || got.VAT.Value.VAT == nil || *got.VAT.Value.VAT != 16 ||
		got.VAT.Value.Deduction == nil || *got.VAT.Value.Deduction != 0 || !got.VAT.Value.NonEmpty {
		t.Fatalf("nested VAT defaults were not represented: %#v", got.VAT)
	}
}

func TestPaymentCategoryEmptyVATIsDistinguishedFromCreateDefaults(t *testing.T) {
	got, err := ParsePaymentCategoryWrite(domain.RequestObject{
		{Name: "name", Value: "Empty VAT"},
		{Name: "vat_management", Value: domain.RequestObject{}},
	}, false)
	if err != nil || !got.VAT.Present || got.VAT.Value.NonEmpty || got.VAT.Value.VAT == nil || *got.VAT.Value.VAT != 0 {
		t.Fatalf("empty POST VAT defaults: %#v %v", got, err)
	}
	got, err = ParsePaymentCategoryWrite(domain.RequestObject{
		{Name: "vat_management", Value: domain.RequestObject{}},
	}, true)
	if err != nil || !got.VAT.Present || got.VAT.Value.NonEmpty {
		t.Fatalf("empty PATCH VAT must be a no-op: %#v %v", got, err)
	}
	got, err = ParsePaymentCategoryWrite(domain.RequestObject{
		{Name: "vat_management", Value: domain.RequestObject{{Name: "unknown", Value: true}}},
	}, true)
	if err != nil || !got.VAT.Present || got.VAT.Value.NonEmpty {
		t.Fatalf("unknown-only PATCH VAT must be a no-op: %#v %v", got, err)
	}
}

func TestPaymentCategoryPatchPreservesOmittedNullFalseAndZero(t *testing.T) {
	body := domain.RequestObject{
		{Name: "expense", Value: false},
		{Name: "archived", Value: false},
		{Name: "type", Value: json.Number("1")},
		{Name: "sport_association", Value: nil},
		{Name: "unknown", Value: "ignored"},
	}
	got, err := ParsePaymentCategoryWrite(body, true)
	if err != nil {
		t.Fatal(err)
	}
	if got.Name != nil || got.NamePresent || got.Expense == nil || *got.Expense ||
		got.Archived == nil || *got.Archived || got.Type == nil || *got.Type != 1 {
		t.Fatalf("partial presence was lost: %#v", got)
	}
	if !got.AssociationPresent || got.AssociationID != nil || got.VAT.Present {
		t.Fatalf("explicit null was not preserved: %#v", got)
	}
	if got.TaxDeductible != nil || got.TaxDeductiblePresent {
		t.Fatal("omitted field was treated as false")
	}
}

func TestPaymentCategoryValidationLimits(t *testing.T) {
	tests := []struct {
		name  string
		body  any
		field string
	}{
		{"missing name", domain.RequestObject{}, "name"},
		{"null name", domain.RequestObject{{Name: "name", Value: nil}}, "name"},
		{"blank name", domain.RequestObject{{Name: "name", Value: "  "}}, "name"},
		{"long name", domain.RequestObject{{Name: "name", Value: repeatRune("è", 256)}}, "name"},
		{"bad UUID", domain.RequestObject{{Name: "name", Value: "ok"}, {Name: "payment_category_id", Value: "not-a-uuid"}}, "payment_category_id"},
		{"bad choice", domain.RequestObject{{Name: "name", Value: "ok"}, {Name: "type", Value: json.Number("3")}}, "type"},
		{"null boolean", domain.RequestObject{{Name: "name", Value: "ok"}, {Name: "expense", Value: nil}}, "expense"},
		{"bad boolean", domain.RequestObject{{Name: "name", Value: "ok"}, {Name: "deleted", Value: "maybe"}}, "deleted"},
		{"VAT shape", domain.RequestObject{{Name: "name", Value: "ok"}, {Name: "vat_management", Value: "bad"}}, "vat_management"},
		{"VAT choice", domain.RequestObject{{Name: "name", Value: "ok"}, {Name: "vat_management", Value: domain.RequestObject{{Name: "vat", Value: json.Number("17")}}}}, "vat_management"},
		{"bool is not a VAT choice", domain.RequestObject{{Name: "name", Value: "ok"}, {Name: "vat_management", Value: domain.RequestObject{{Name: "vat", Value: true}}}}, "vat_management"},
		{"VAT null", domain.RequestObject{{Name: "name", Value: "ok"}, {Name: "vat_management", Value: nil}}, "vat_management"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			_, err := ParsePaymentCategoryWrite(tc.body, false)
			var validation *PaymentCategoryValidationError
			if !errors.As(err, &validation) {
				t.Fatalf("want %s validation error, got %#v", tc.field, err)
			}
			messages, _ := validation.Errors[tc.field].([]string)
			if len(messages) == 0 && tc.field != "vat_management" {
				t.Fatalf("want %s validation error, got %#v", tc.field, err)
			}
		})
	}
}

func TestPaymentCategoryRejectsNonObjectBody(t *testing.T) {
	for _, body := range []any{nil, []any{}, "text"} {
		_, err := ParsePaymentCategoryWrite(body, false)
		var bodyErr *PaymentCategoryBodyError
		if !errors.As(err, &bodyErr) {
			t.Fatalf("accepted body %#v: %v", body, err)
		}
	}
}

func TestPaymentCategoryVATAggregatesNestedErrors(t *testing.T) {
	_, err := ParsePaymentCategoryWrite(domain.RequestObject{
		{Name: "name", Value: "Invalid VAT"},
		{Name: "type", Value: json.Number("9")},
		{Name: "vat_management", Value: domain.RequestObject{
			{Name: "vat", Value: json.Number("99")},
			{Name: "deduction", Value: json.Number("99")},
		}},
	}, false)
	validation, ok := err.(*PaymentCategoryValidationError)
	if !ok {
		t.Fatal(err)
	}
	nested, ok := validation.Errors["vat_management"].(map[string]any)
	if !ok || len(nested["vat"].([]string)) == 0 || len(nested["deduction"].([]string)) == 0 || len(validation.Errors["type"].([]string)) == 0 {
		t.Fatal(validation.Errors)
	}
}

func TestPaymentCategoryCreateOverridesAssociationInput(t *testing.T) {
	for _, value := range []any{nil, "not-a-uuid", domain.RequestObject{{Name: "value", Value: "foreign"}}} {
		got, err := ParsePaymentCategoryWrite(domain.RequestObject{
			{Name: "name", Value: "Owned"},
			{Name: "sport_association", Value: value},
			{Name: "vat_management", Value: domain.RequestObject{}},
		}, false)
		if err != nil || !got.AssociationPresent || got.AssociationID != nil {
			t.Fatalf("CREATE association input was not overridden: %#v %v", got, err)
		}
	}
}

func TestPaymentCategoryResponseNullVATAndZeroUUID(t *testing.T) {
	zero := "00000000-0000-0000-0000-000000000000"
	got := PaymentCategoryFromState(domain.PaymentCategoryState{
		ID: "00000000-0000-4000-8000-000000000001", AssociationID: &zero,
		Name: "Legacy", CreationDate: time.Date(2026, 10, 23, 12, 0, 0, 0, time.UTC), Type: 1,
	})
	if got.AssociationID == nil || *got.AssociationID != zero || got.VAT != nil {
		t.Fatalf("historical null/zero relationships were not preserved: %#v", got)
	}
	if !reflect.DeepEqual(got.JSONMap()["vat_management"], nil) {
		t.Fatal("null VAT must serialize as null")
	}
}

func TestPaymentCategoryResponseTimeMatchesDjangoMicroseconds(t *testing.T) {
	for value, expected := range map[time.Time]string{
		time.Date(2026, 1, 2, 3, 4, 5, 100000000, time.UTC): "2026-01-02T03:04:05.100000Z",
		time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC):         "2026-01-02T03:04:05Z",
	} {
		got := PaymentCategoryFromState(domain.PaymentCategoryState{CreationDate: value})
		if got.CreationDate != expected {
			t.Fatal(got.CreationDate, expected)
		}
	}
}

func repeatRune(value string, count int) string {
	raw := make([]rune, count)
	for i := range raw {
		raw[i] = []rune(value)[0]
	}
	return string(raw)
}
