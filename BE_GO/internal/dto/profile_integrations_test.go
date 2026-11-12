package dto

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestParseProfileIntegrations(t *testing.T) {
	object := func(fields ...domain.RequestField) domain.RequestObject { return domain.RequestObject(fields) }
	field := func(name string, value any) domain.RequestField { return domain.RequestField{Name: name, Value: value} }
	valid := []struct {
		name    string
		body    any
		url     *string
		enabled bool
		urlSet  bool
		onSet   bool
	}{
		{"empty", object(), nil, false, false, false},
		{"text and boolean", object(field("review_url", " https://a.example "), field("review_url_enabled", "on")), ptr("https://a.example"), true, true, true},
		{"number is text", object(field("review_url", json.Number("5")), field("review_url_enabled", json.Number("0"))), ptr("5"), false, true, true},
		{"null url", object(field("review_url", nil)), nil, false, true, false},
		{"yes", object(field("review_url_enabled", "Yes")), nil, true, false, true},
	}
	for _, test := range valid {
		input, invalid := ParseProfileIntegrations(test.body, false)
		if invalid != nil {
			t.Fatalf("%s: unexpected errors %v", test.name, invalid)
		}
		if input.URLPresent != test.urlSet || input.EnabledPresent != test.onSet || input.Enabled != test.enabled ||
			(input.URL == nil) != (test.url == nil) || (test.url != nil && *input.URL != *test.url) {
			t.Fatalf("%s: got %+v", test.name, input)
		}
	}
	refused := []struct {
		name string
		body any
		want string
	}{
		{"boolean spelling", object(field("review_url_enabled", "TrUe")), "review_url_enabled"},
		{"null boolean", object(field("review_url_enabled", nil)), "review_url_enabled"},
		{"list boolean", object(field("review_url_enabled", []any{true})), "review_url_enabled"},
		{"long url", object(field("review_url", strings.Repeat("u", 256))), "review_url"},
		{"list url", object(field("review_url", []any{"a"})), "review_url"},
		{"boolean url", object(field("review_url", true)), "review_url"},
		{"list body", []any{}, "non_field_errors"},
	}
	for _, test := range refused {
		if _, invalid := ParseProfileIntegrations(test.body, false); invalid[test.want] == nil {
			t.Fatalf("%s: want an error for %s, got %v", test.name, test.want, invalid)
		}
	}
}

func ptr(text string) *string { return &text }
