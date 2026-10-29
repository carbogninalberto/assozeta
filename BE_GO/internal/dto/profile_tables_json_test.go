package dto

import (
	"reflect"
	"testing"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func FuzzProfileTablesJSON(f *testing.F) {
	for _, seed := range []string{`"\ud800"`, `"\\ud800"`, `{"tables_settings":{"\udfff":"\ud800"}}`, `"\ud83d\ude00"`, `{"x":[true,null,1.5]}`, `"unterminated`, `"\uZZZZ"`} {
		f.Add([]byte(seed))
	}
	var invalid func(any) bool
	invalid = func(value any) bool {
		switch v := value.(type) {
		case string:
			return !utf8.ValidString(v)
		case domain.RequestObject:
			for _, field := range v {
				if !utf8.ValidString(field.Name) || invalid(field.Value) {
					return true
				}
			}
		case []any:
			for _, item := range v {
				if invalid(item) {
					return true
				}
			}
		}
		return false
	}
	f.Fuzz(func(t *testing.T, data []byte) {
		if len(data) > 65536 {
			return
		}
		got, err := ParseProfileTablesJSON(data)
		ordinary, ordinaryErr := ParseReferenceJSON(data)
		if (err == nil) != (ordinaryErr == nil) {
			t.Fatalf("JSON syntax acceptance changed: %q, %v, %v", data, err, ordinaryErr)
		}
		if err == nil && !invalid(got) && !reflect.DeepEqual(got, ordinary) {
			t.Fatalf("valid JSON changed: %q", data)
		}
	})
}

func TestProfileTablesJSONPreservesSurrogateFailure(t *testing.T) {
	for _, raw := range []string{`"\ud800"`, `"\udfff"`, `"x\uD800y"`, `"\ud800\ud800"`, `"\ud800\u0061"`, `"\udc00\ud800"`} {
		got, err := ParseProfileTablesJSON([]byte(raw))
		if err != nil {
			t.Fatal(raw, err)
		}
		value, ok := got.(string)
		if !ok || utf8.ValidString(value) {
			t.Fatal("surrogate silently replaced", raw, got)
		}
	}
	for _, tc := range []struct{ raw, want string }{{`"\ufffd"`, "�"}, {`"\ud83d\ude00"`, "😀"}, {`"\\ud800"`, `\ud800`}, {`"a\n\"\u00e8"`, "a\n\"è"}} {
		got, err := ParseProfileTablesJSON([]byte(tc.raw))
		if err != nil || got != tc.want {
			t.Fatal(tc, got, err)
		}
	}
	got, err := ParseProfileTablesJSON([]byte(`{"\ud800":1,"tables_settings":{"\udfff":"x\ud800"},"unused":"\ud800"}`))
	if err != nil {
		t.Fatal(err)
	}
	body := got.(domain.RequestObject)
	if utf8.ValidString(body[0].Name) {
		t.Fatal("invalid key lost")
	}
	data, _ := body.Lookup("tables_settings")
	field := data.(domain.RequestObject)[0]
	if utf8.ValidString(field.Name) || utf8.ValidString(field.Value.(string)) {
		t.Fatal(field)
	}
	ordinary, err := ParseReferenceJSON([]byte(`"\ud800"`))
	if err != nil || ordinary != "�" {
		t.Fatal("other parsers changed", ordinary, err)
	}
	for _, raw := range []string{`"\uZZZZ"`, `"unterminated`, `{"a":1,}`, `"\x80"`} {
		if _, err := ParseProfileTablesJSON([]byte(raw)); err == nil {
			t.Fatal("invalid JSON accepted", raw)
		}
	}
}
