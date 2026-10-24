package dto

import (
	"encoding/json"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestCollaboratorJSONPreservesFieldTypes(t *testing.T) {
	for _, tc := range []struct{ raw, want string }{
		{`{"values":[true,1,1.0,9007199254740993,9007199254740993.0,null]}`, `{"values":[true,1,1.0,9007199254740993,9.007199254740992e+15,null]}`},
		{`0.000`, `0.0`}, {`false`, `false`}, {`"arbitrary stored string"`, `"arbitrary stored string"`}, {`null`, `null`},
	} {
		value, err := collaboratorJSON([]byte(tc.raw))
		if err != nil {
			t.Fatal(err)
		}
		encoded, err := json.Marshal(value)
		if err != nil || string(encoded) != tc.want {
			t.Fatalf("%s -> %s, %v", tc.raw, encoded, err)
		}
	}
	if _, err := collaboratorJSON([]byte(`1e400`)); err == nil {
		t.Fatal("nonfinite JSON accepted")
	}
}
func TestCollaboratorEmptyDetailAndListDiscriminator(t *testing.T) {
	value, _ := json.Marshal(EmptyCollaborator())
	if string(value) != `{"user_id":null,"first_name":"","last_name":"","email":"","phone":"","role":null,"connected_user":null,"collaborator_role":null,"collaborator_permissions":null}` {
		t.Fatal(string(value))
	}
	detail, err := CollaboratorFromRecord(domain.CollaboratorRecord{}, false)
	if err != nil || detail.IsInvite != nil {
		t.Fatal(detail, err)
	}
	list, err := CollaboratorFromRecord(domain.CollaboratorRecord{}, true)
	if err != nil || list.IsInvite == nil || *list.IsInvite {
		t.Fatal(list, err)
	}
}
