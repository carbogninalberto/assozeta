package service

import (
	"encoding/json"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/stripeapi"
)

// The expected strings are CPython 3.13's, computed with stripe-python 11.6.0:
//
//	StripeObject.construct_from({'b': 2, 'a': [1, {'z': True}], 'n': None,
//	    's': 'Città'}, 'sk_test')
//
// str() is json.dumps(..., sort_keys=True, indent=2); a list id is repr() of
// the list, where each nested object is StripeObject.__repr__ with hex(id())
// replaced by owner decision X18's fixed placeholder.
func TestStripeIDPythonStr(t *testing.T) {
	objectID := domain.RequestObject{
		{Name: "b", Value: json.Number("2")},
		{Name: "a", Value: []any{json.Number("1"), domain.RequestObject{{Name: "z", Value: true}}}},
		{Name: "n", Value: nil},
		{Name: "s", Value: "Città"},
	}
	wantObject := "{\n  \"a\": [\n    1,\n    {\n      \"z\": true\n    }\n  ],\n  \"b\": 2,\n  \"n\": null,\n  \"s\": \"Citt\\u00e0\"\n}"
	listID := []any{
		domain.RequestObject{{Name: "z", Value: true}, {Name: "a", Value: json.Number("1")}},
		json.Number("2"), "x", nil, true, json.Number("1.5"),
		[]any{domain.RequestObject{{Name: "k", Value: "v"}}, []any{json.Number("1"), json.Number("2")}},
	}
	wantList := "[<StripeObject at 0x0000000000000000> JSON: {\n  \"a\": 1,\n  \"z\": true\n}, 2, 'x', None, True, 1.5, " +
		"[<StripeObject at 0x0000000000000000> JSON: {\n  \"k\": \"v\"\n}, [1, 2]]]"
	for name, tc := range map[string]struct {
		value any
		want  string
	}{"object": {objectID, wantObject}, "list": {listID, wantList}} {
		t.Run(name, func(t *testing.T) {
			reply := stripeapi.Object{{Name: "id", Value: tc.value}}
			got, err := stripeID(reply)
			if err != nil {
				t.Fatalf("stripeID: %v", err)
			}
			if got == nil || *got != tc.want {
				text := "<nil>"
				if got != nil {
					text = *got
				}
				t.Fatalf("stripeID = %q, want %q", text, tc.want)
			}
		})
	}
}

// StripeObject.__repr__ names the class convert_to_stripe_object picks, the
// "object" string and the string id (stripe-python 11.6.0's output, with the
// placeholder address).
func TestStripeIDListReprIdentParts(t *testing.T) {
	id := []any{
		domain.RequestObject{{Name: "id", Value: "x1"}, {Name: "object", Value: "charge"}},
		domain.RequestObject{{Name: "id", Value: json.Number("5")}, {Name: "object", Value: json.Number("7")}},
		domain.RequestObject{{Name: "object", Value: "nope"}, {Name: "id", Value: "y"}},
	}
	want := "[<Charge charge id=x1 at 0x0000000000000000> JSON: {\n  \"id\": \"x1\",\n  \"object\": \"charge\"\n}, " +
		"<StripeObject at 0x0000000000000000> JSON: {\n  \"id\": 5,\n  \"object\": 7\n}, " +
		"<StripeObject nope id=y at 0x0000000000000000> JSON: {\n  \"id\": \"y\",\n  \"object\": \"nope\"\n}]"
	got, err := stripeID(stripeapi.Object{{Name: "id", Value: id}})
	if err != nil || got == nil || *got != want {
		t.Fatalf("stripeID = %v, %v\nwant %q", got, err, want)
	}
}
