package dto

import (
	"encoding/json"
	"errors"
	"reflect"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestInstanceAdminKeysWithin(t *testing.T) {
	object := func(names ...string) domain.RequestObject {
		out := domain.RequestObject{}
		for _, name := range names {
			out = append(out, domain.RequestField{Name: name, Value: "x"})
		}
		return out
	}
	cases := []struct {
		name   string
		body   any
		within bool
		err    error
	}{
		{"oem", object("oem"), true, nil},
		{"version", object("uiApiVersion"), true, nil},
		{"both", object("oem", "uiApiVersion"), true, nil},
		{"empty object", object(), false, nil},
		{"extra key", object("uiApiVersion", "meta_title"), false, nil},
		{"list of keys", []any{"uiApiVersion", "oem"}, true, nil},
		{"list with number", []any{"uiApiVersion", json.Number("1")}, false, nil},
		{"empty list", []any{}, false, nil},
		{"list holding a list", []any{"oem", []any{"oem"}}, false, ErrInstanceAdminUnhashable},
		{"string body", "uiApiVersion", false, nil},
		{"empty string", "", false, nil},
		{"number", json.Number("5"), false, ErrInstanceAdminUnhashable},
		{"null", nil, false, ErrInstanceAdminUnhashable},
		{"bool", true, false, ErrInstanceAdminUnhashable},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			within, err := InstanceAdminKeysWithin(tc.body, "oem", "uiApiVersion")
			if within != tc.within || !errors.Is(err, tc.err) {
				t.Fatalf("got (%v, %v), want (%v, %v)", within, err, tc.within, tc.err)
			}
		})
	}
}

func TestInstanceAdminEditUIAPIVersion(t *testing.T) {
	body := func(fields ...domain.RequestField) domain.RequestObject { return domain.RequestObject(fields) }
	version := func(value any) domain.RequestField { return domain.RequestField{Name: "uiApiVersion", Value: value} }
	valid := []struct {
		name string
		body domain.RequestObject
		want map[string]string
	}{
		{"v2", body(version("v2")), map[string]string{"ui_api_version": "v2"}},
		{"v1", body(version("v1")), map[string]string{"ui_api_version": "v1"}},
		{"with branding", body(domain.RequestField{Name: "oem", Value: body(domain.RequestField{Name: "name", Value: " Nome "})}, version("v2")),
			map[string]string{"name": "Nome", "ui_api_version": "v2"}},
		{"empty oem", body(domain.RequestField{Name: "oem", Value: domain.RequestObject{}}), map[string]string{}},
	}
	for _, tc := range valid {
		t.Run(tc.name, func(t *testing.T) {
			got, err := InstanceAdminEdit(tc.body)
			if err != nil || !reflect.DeepEqual(got, tc.want) {
				t.Fatalf("got (%v, %v), want %v", got, err, tc.want)
			}
		})
	}
	invalid := []struct {
		name  string
		value any
		want  []string
	}{
		{"unknown", "v3", []string{"choice:v3"}},
		{"blank", "", []string{"choice:"}},
		{"padded", " v2", []string{"choice: v2"}},
		{"null", nil, []string{PaymentCategoryValidationNull}},
		{"bool", true, []string{"choice:True"}},
		{"int", json.Number("2"), []string{"choice:2"}},
		{"float", json.Number("2.0"), []string{"choice:2.0"}},
		{"list", []any{"v2"}, []string{"choice:['v2']"}},
		{"dict", body(domain.RequestField{Name: "v", Value: json.Number("2")}, domain.RequestField{Name: "x", Value: nil}), []string{"choice:{'v': 2, 'x': None}"}},
	}
	for _, tc := range invalid {
		t.Run(tc.name, func(t *testing.T) {
			_, err := InstanceAdminEdit(body(version(tc.value)))
			var failure *InstanceAdminInvalid
			if !errors.As(err, &failure) || !reflect.DeepEqual(failure.Body, map[string]any{"uiApiVersion": tc.want}) {
				t.Fatalf("got %#v", err)
			}
		})
	}
	_, err := InstanceAdminEdit(body(domain.RequestField{Name: "oem", Value: nil}, version("v9")))
	var failure *InstanceAdminInvalid
	if !errors.As(err, &failure) || !reflect.DeepEqual(failure.Body, map[string]any{
		"oem": []string{PaymentCategoryValidationNull}, "uiApiVersion": []string{"choice:v9"}}) {
		t.Fatalf("combined errors: %#v", err)
	}
}
