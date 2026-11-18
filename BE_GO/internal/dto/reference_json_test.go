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

// TestJSONValueDepth pins the depth json.dumps walks: it equals the text's own
// depth for a value without duplicate keys, and a duplicate key's overwritten
// deep value no longer counts.
func TestJSONValueDepth(t *testing.T) {
	for _, test := range []struct {
		text      string
		depth     int
		textDepth int // 0: the value depth equals the text depth
	}{
		{`1`, 0, 0},
		{`[]`, 1, 0},
		{`{"a": 1}`, 1, 0},
		{`{"a": {"a": [1]}}`, 3, 0},
		{`{"amount": [[[1]]], "amount": "1"}`, 1, 4},
		{strings.Repeat(`{"a":`, 600) + `1` + strings.Repeat(`}`, 600), 600, 0},
	} {
		value, err := ParseReferenceJSON([]byte(test.text))
		if err != nil {
			t.Fatalf("%.20s: %v", test.text, err)
		}
		wantText := test.textDepth
		if wantText == 0 {
			wantText = test.depth
		}
		if got := JSONValueDepth(value); got != test.depth || AuditJSONDepth(test.text) != wantText {
			t.Fatalf("%.20s: value depth %d (text %d), want %d (text %d)", test.text, got, AuditJSONDepth(test.text),
				test.depth, wantText)
		}
	}
}

func TestReferenceJSONRejectsMalformedAndExcessivelyDeepInputs(t *testing.T) {
	for _, input := range []string{"", "{}{}", `{"id":`, `[1,]`, strings.Repeat("[", 1002) + "0" + strings.Repeat("]", 1002)} {
		if _, err := ParseReferenceJSON([]byte(input)); err == nil {
			t.Fatal("malformed/deep reference input accepted")
		}
	}
}
