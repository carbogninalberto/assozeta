package dto

import (
	"encoding/json"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// PrimaryKeyRelatedField on a UUID key: only bool is an incorrect type; every
// value UUIDField.to_python rejects quotes str(value) (the list message was
// observed on Django in the profile-settings family).
func TestSupplierRelatedFollowsUUIDFieldToPython(t *testing.T) {
	cases := []struct {
		value    any
		id, code string
	}{
		{"A1000000-0000-4000-8000-000000977001", "a1000000-0000-4000-8000-000000977001", ""},
		{json.Number("5"), "00000000-0000-0000-0000-000000000005", ""},
		{true, "", "related-incorrect-type:bool"},
		{"abc", "", "related-invalid-uuid:abc"},
		{[]any{"a1000000-0000-4000-8000-000000977001"}, "", "related-invalid-uuid:['a1000000-0000-4000-8000-000000977001']"},
		{domain.RequestObject{{Name: "a", Value: json.Number("1")}}, "", "related-invalid-uuid:{'a': 1}"},
		{json.Number("1.5"), "", "related-invalid-uuid:1.5"},
		{json.Number("-1"), "", "related-invalid-uuid:-1"},
		{json.Number("340282366920938463463374607431768211456"), "", "related-invalid-uuid:340282366920938463463374607431768211456"},
	}
	for _, test := range cases {
		id, code := supplierRelated(test.value)
		if id != test.id || code != test.code {
			t.Errorf("supplierRelated(%#v) = %q, %q; want %q, %q", test.value, id, code, test.id, test.code)
		}
	}
}
