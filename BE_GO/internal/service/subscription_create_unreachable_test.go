package service

import (
	"encoding/json"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// TestSubscriptionCreateUnreachableIsAppState pins the new sentinel: a state
// only the application itself writes answers Django's 500 page from Go and is
// never handed off.
func TestSubscriptionCreateUnreachableIsAppState(t *testing.T) {
	err := domain.ErrSubscriptionCreateUnreachable
	wrapped := errors.Join(errors.New("subscription/add"), err)
	if !errors.Is(wrapped, refusal.ErrAppState) {
		t.Fatalf("%v does not match ErrAppState", err)
	}
	if reason, ok := refusal.AppStateReason(wrapped); !ok || reason != "subscription create: unreachable state" {
		t.Fatalf("app-state reason %q %v", reason, ok)
	}
	if errors.Is(wrapped, refusal.ErrNotPorted) {
		t.Fatalf("%v is still a refusal: it would be handed off", err)
	}
	if !errors.Is(wrapped, domain.ErrSubscriptionCreateUnreachable) {
		t.Fatalf("%v no longer matches itself", err)
	}
}

// TestSubscriptionQuickID ports UUIDField.to_python: a text through
// uuid.UUID(hex), an int (a bool included) through uuid.UUID(int) when it is
// in 0..2**128-1; every other shape is a ValidationError or finds no row: the
// 500 page.
func TestSubscriptionQuickID(t *testing.T) {
	cases := []struct {
		name  string
		value any
		want  string
	}{
		{name: "text", value: "a1000000-0000-4000-8000-000000700001", want: "a1000000-0000-4000-8000-000000700001"},
		{name: "text upper case", value: "A1000000-0000-4000-8000-000000700001", want: "a1000000-0000-4000-8000-000000700001"},
		{name: "integer", value: json.Number("42"), want: "00000000-0000-0000-0000-00000000002a"},
		{name: "zero", value: json.Number("0"), want: "00000000-0000-0000-0000-000000000000"},
		{name: "true", value: true, want: "00000000-0000-0000-0000-000000000001"},
		{name: "false", value: false, want: "00000000-0000-0000-0000-000000000000"},
		{name: "largest integer", value: json.Number("340282366920938463463374607431768211455"), want: "ffffffff-ffff-ffff-ffff-ffffffffffff"},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			got, err := subscriptionQuickID(test.value)
			if err != nil || got != test.want {
				t.Fatalf("got %q %v, want %q", got, err, test.want)
			}
		})
	}
	for name, value := range map[string]any{
		"malformed text": "non-un-uuid",
		"float":          json.Number("1.5"),
		"exponent":       json.Number("1e3"),
		"negative":       json.Number("-1"),
		"past 128 bits":  json.Number("340282366920938463463374607431768211456"),
		"none":           nil,
		"list":           []any{json.Number("1")},
		"object":         domain.RequestObject{{Name: "id", Value: json.Number("1")}},
	} {
		t.Run(name, func(t *testing.T) {
			if id, err := subscriptionQuickID(value); !errors.Is(err, domain.ErrSubscriptionCreateServer) || id != "" {
				t.Fatalf("got %q %v, want the server error", id, err)
			}
		})
	}
}
