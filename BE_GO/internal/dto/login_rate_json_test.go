package dto

import (
	"encoding/json"
	"math"
	"reflect"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestLoginRateConstantsDoNotChangeStrictJSONParser(t *testing.T) {
	for _, tc := range []struct {
		raw  string
		sign int
	}{{"NaN", 0}, {"Infinity", 1}, {"-Infinity", -1}} {
		value, err := ParseLoginRateJSON([]byte("[ " + tc.raw + " ,\"" + tc.raw + "\"]"))
		if err != nil {
			t.Fatal(err)
		}
		items := value.([]any)
		number, ok := items[0].(float64)
		if !ok || (tc.sign == 0 && !math.IsNaN(number)) || (tc.sign != 0 && !math.IsInf(number, tc.sign)) || items[1] != tc.raw {
			t.Fatal("constant or quoted text changed")
		}
		if _, err := ParseReferenceJSON([]byte(tc.raw)); err == nil {
			t.Fatal("strict view parser admitted a rate-only constant")
		}
	}
	for _, raw := range []string{"-NaN", "nan", "+Infinity", "NaN0", "Infinity.0", "[NaN NaN]", "{NaN:1}", "[Infinity,]", "[NaN] trailing"} {
		if _, err := ParseLoginRateJSON([]byte(raw)); err == nil {
			t.Fatalf("invalid token accepted: %q", raw)
		}
	}
	for _, raw := range []string{`{"username":"NaN","quoted":"\\\"Infinity","number":1.25}`, `{"username":{"z":2,"a":null,"z":3}}`, `[true,false,null,-1,1e400]`} {
		strict, err := ParseReferenceJSON([]byte(raw))
		if err != nil {
			t.Fatal(err)
		}
		rate, err := ParseLoginRateJSON([]byte(raw))
		if err != nil || !reflect.DeepEqual(strict, rate) {
			t.Fatal("ordinary JSON differs")
		}
	}
}

func FuzzLoginRateQuotedStrings(f *testing.F) {
	for _, seed := range []string{"NaN", "Infinity", "-Infinity", "\\\"NaN", "[NaN]", "\u2028NaN", ""} {
		f.Add(seed)
	}
	f.Fuzz(func(t *testing.T, value string) {
		raw, err := json.Marshal(map[string]string{"username": value})
		if err != nil {
			t.Fatal(err)
		}
		// encoding/json replaces invalid UTF-8 on marshal; compare the same input
		// document in both parsers, not the pre-marshaled Go string.
		strict, err := ParseReferenceJSON(raw)
		if err != nil {
			t.Fatal(err)
		}
		parsed, err := ParseLoginRateJSON(raw)
		if err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(strict.(domain.RequestObject), parsed) {
			t.Fatal("quoted content was rewritten")
		}
	})
}
