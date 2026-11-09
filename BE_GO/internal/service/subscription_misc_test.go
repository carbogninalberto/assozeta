package service

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// Expected values from fuzzywuzzy 0.18.0 (difflib) and Python 3.13 str.lower().
func TestSubscriptionMiscFuzzRatio(t *testing.T) {
	for _, item := range []struct {
		a, b string
		want int
	}{
		{"palestra olimpia", "palestra olimpya", 94}, {"palestra olimpia", "palazzo dello sport", 34},
		{"palestra olimpia", "palestra olimpia ", 97}, {"ali", "ali", 100}, {"", "x", 0}, {"", "", 100}, {"abcd", "dcba", 25},
		{"karate club", "club karate", 55}, {strings.Repeat("a", 150) + strings.Repeat("b", 100), strings.Repeat("b", 100) + strings.Repeat("a", 150), 0},
		{"dojo", "d", 40},
	} {
		if got := fuzzRatio(item.a, item.b); got != item.want {
			t.Errorf("fuzzRatio(%q, %q) = %d, want %d", item.a, item.b, got, item.want)
		}
	}
}

func TestSubscriptionMiscPythonLower(t *testing.T) {
	for input, want := range map[string]string{"İSTANBUL": "i̇stanbul", "ΟΔΟΣ": "οδος", "Σ": "σ", "ΑΣ Β": "ας β", "Palestra": "palestra"} {
		if got := pythonLower(input); got != want {
			t.Errorf("pythonLower(%q) = %q, want %q", input, got, want)
		}
	}
}

func TestSubscriptionMiscTaxCodeInputs(t *testing.T) {
	misc := &SubscriptionMisc{now: func() time.Time { return time.Date(2026, 11, 9, 0, 0, 0, 0, time.UTC) }}
	person := func(changes ...domain.RequestField) domain.RequestObject {
		body := domain.RequestObject{{Name: "last_name", Value: "Rossi"}, {Name: "first_name", Value: "Mario"}, {Name: "sex", Value: "M"},
			{Name: "born_date", Value: "10/05/1990"}, {Name: "born_city", Value: "Roma"}}
		for _, change := range changes {
			body = dto.SetRequestField(body, change.Name, change.Value)
		}
		return body
	}
	result, err := misc.TaxCode(person())
	if err != nil || result.(domain.RequestObject)[0].Value != "RSSMRA90E10H501G" {
		t.Fatalf("TaxCode = %v, %v", result, err)
	}
	for _, item := range []struct {
		body any
		want error
	}{
		{person(domain.RequestField{Name: "born_city", Value: "Atlantide"}), ErrSubscriptionMiscUnmapped},
		{person(domain.RequestField{Name: "sex", Value: json.Number("1")}), domain.ErrSubscriptionMiscServer},
		{person(domain.RequestField{Name: "sex", Value: "X"}), ErrSubscriptionMiscMissingData},
		{person(domain.RequestField{Name: "last_name", Value: json.Number("5")}), ErrSubscriptionMiscInvalidData},
		{person(domain.RequestField{Name: "last_name", Value: "王"}), domain.ErrSubscriptionMiscNotPorted},
		{person(domain.RequestField{Name: "born_date", Value: "10 maggio 1990"}), domain.ErrSubscriptionMiscNotPorted},
		{[]any{"Rossi"}, domain.ErrSubscriptionMiscServer},
	} {
		if _, err := misc.TaxCode(item.body); !errors.Is(err, item.want) {
			t.Errorf("TaxCode(%v) error = %v, want %v", item.body, err, item.want)
		}
	}
}
