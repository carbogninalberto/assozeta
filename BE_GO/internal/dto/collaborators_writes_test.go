package dto

import (
	"encoding/json"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestCollaboratorRole(t *testing.T) {
	for _, item := range []struct {
		value   any
		present bool
		role    int64
		ok      bool
	}{
		{nil, false, 3, true}, {json.Number("2"), true, 2, true}, {json.Number("2.9"), true, 2, true},
		{json.Number("99"), true, 1, true}, {json.Number("100000000000000000000000"), true, 1, true}, {true, true, 1, true},
		{false, true, 1, true}, {" 3 ", true, 3, true}, {"0_2", true, 2, true}, {"-3", true, 1, true},
		{"2.0", true, 0, false}, {"", true, 0, false}, {nil, true, 0, false}, {[]any{}, true, 0, false},
		{domain.RequestObject{}, true, 0, false}, {json.Number("1e400"), true, 0, false},
	} {
		role, ok := CollaboratorRole(item.value, item.present)
		if role != item.role || ok != item.ok {
			t.Errorf("CollaboratorRole(%#v, %v) = %d, %v; want %d, %v", item.value, item.present, role, ok, item.role, item.ok)
		}
	}
}
