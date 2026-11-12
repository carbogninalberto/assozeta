package dto

import (
	"encoding/json"
	"reflect"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestSubscriptionCreateDecimal(t *testing.T) {
	for value, want := range map[float64]string{25: "25.00", 52.5: "52.50", 0: "0.00", 0.1 + 0.2: "0.30", 12.5: "12.50"} {
		if got, ok := SubscriptionCreateDecimal(value); !ok || got != want {
			t.Errorf("%v: got %q %v, want %q", value, got, ok, want)
		}
	}
	if _, ok := SubscriptionCreateDecimal(0.125); ok {
		t.Error("a third decimal place must be refused")
	}
}

func TestSubscriptionCreateBornDate(t *testing.T) {
	for text, ok := range map[string]bool{"15/03/1990": true, "1/3/1990": true, " 1/3/1990": true, "31/04/1990": false, "1990-03-15": false,
		"00/03/1990": false, "15/13/1990": false, "15/03/90": false} {
		if _, got := SubscriptionCreateBornDate(text); got != ok {
			t.Errorf("%q: got %v, want %v", text, got, ok)
		}
	}
}

func TestParseSignatureRequest(t *testing.T) {
	cases := []struct {
		value any
		want  map[string][]string
	}{
		{nil, map[string][]string{"non_field_errors": {SubscriptionCreateNoData}}},
		{[]any{}, map[string][]string{"non_field_errors": {"dictionary:list"}}},
		{domain.RequestObject{{Name: "there_is_signature", Value: false}}, map[string][]string{"data": {PaymentCategoryValidationRequired}}},
		{domain.RequestObject{{Name: "there_is_signature", Value: true}, {Name: "data", Value: ""}}, map[string][]string{"data": {SubscriptionCreateSignature}}},
		{domain.RequestObject{{Name: "there_is_signature", Value: "maybe"}, {Name: "data", Value: true}},
			map[string][]string{"there_is_signature": {SubscriptionCreateBoolean}, "data": {PaymentCategoryValidationString}}},
		{domain.RequestObject{{Name: "there_is_signature", Value: json.Number("0")}, {Name: "data", Value: nil}}, nil},
	}
	for _, c := range cases {
		if _, got := ParseSignatureRequest(c.value); !reflect.DeepEqual(got, c.want) {
			t.Errorf("%#v: got %v, want %v", c.value, got, c.want)
		}
	}
}
