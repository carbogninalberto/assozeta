package service

import (
	"context"
	"encoding/json"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestProfileJSONAuditRepresentation(t *testing.T) {
	for _, tc := range []struct {
		value any
		want  string
	}{
		{nil, "null"}, {true, "true"},
		{domain.RequestObject{{Name: "z", Value: json.Number("1e0")}, {Name: "a", Value: []any{"è😀", false, nil}}}, `{"a": ["\u00e8\ud83d\ude00", false, null], "z": 1.0}`},
		{map[string]any{"a": json.Number("-0"), "é": "<x>\n\t\""}, `{"a": 0, "\u00e9": "<x>\n\t\""}`},
		{json.Number("1e-7"), "1e-07"}, {json.Number("1e16"), "1e+16"},
	} {
		got, err := profileJSONText(context.Background(), tc.value)
		if err != nil || got != tc.want {
			t.Fatalf("%#v: %q, %v; want %q", tc.value, got, err, tc.want)
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if got, err := profileJSONText(ctx, "x"); got != "" || !errors.Is(err, context.Canceled) {
		t.Fatal("cancelled JSON", got, err)
	}
}

func TestProfileBooleanModelConversion(t *testing.T) {
	for _, value := range []any{true, json.Number("1"), json.Number("1.0"), "t", "True", "1"} {
		if got, err := profileBoolean(value); err != nil || !got {
			t.Fatal(value, got, err)
		}
	}
	for _, value := range []any{false, json.Number("0"), json.Number("0.0"), "f", "False", "0"} {
		if got, err := profileBoolean(value); err != nil || got {
			t.Fatal(value, got, err)
		}
	}
	for _, value := range []any{nil, json.Number("2"), "true", "false", []any{}, domain.RequestObject{}} {
		if _, err := profileBoolean(value); err == nil {
			t.Fatal("invalid boolean admitted", value)
		}
	}
}
