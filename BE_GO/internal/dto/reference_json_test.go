package dto

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestReferenceJSONKeepsOrderDuplicatesAndIntegerPrecision(t *testing.T) {
	value, err := ParseReferenceJSON([]byte(`{"subscription":1,"new_member_info":{"role":2},"subscription":340282366920938463463374607431768211455,"array":[null,false,"id"]}`))
	if err != nil {
		t.Fatal(err)
	}
	object := value.(domain.RequestObject)
	if len(object) != 3 || object[0].Name != "subscription" || object[1].Name != "new_member_info" || object[0].Value != json.Number("340282366920938463463374607431768211455") {
		t.Fatalf("lost reference semantics: %#v", object)
	}
	if _, ok := object[1].Value.(domain.RequestObject); !ok {
		t.Fatal("nested object lost insertion order")
	}
}
func TestReferenceJSONRejectsMalformedAndExcessivelyDeepInputs(t *testing.T) {
	for _, input := range []string{"", "{}{}", `{"id":`, `[1,]`, strings.Repeat("[", 1002) + "0" + strings.Repeat("]", 1002)} {
		if _, err := ParseReferenceJSON([]byte(input)); err == nil {
			t.Fatal("malformed/deep reference input accepted")
		}
	}
}
