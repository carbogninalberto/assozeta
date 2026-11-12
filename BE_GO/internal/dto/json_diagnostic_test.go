package dto

import "testing"

func TestJSONDiagnosticsProductionTrailingComma(t *testing.T) {
	for _, tc := range []struct{ raw, want string }{
		{`[1,]`, "Illegal trailing comma before end of array: line 1 column 3 (char 2)"},
		{`{"a":1,}`, "Illegal trailing comma before end of object: line 1 column 7 (char 6)"},
		{"[1, \n ]", "Illegal trailing comma before end of array: line 1 column 3 (char 2)"},
		{`{"x":[1,]}`, "Illegal trailing comma before end of array: line 1 column 8 (char 7)"},
		{"{\"è\":1, \n }", "Illegal trailing comma before end of object: line 1 column 7 (char 6)"},
	} {
		if actual := JSONParseDetail([]byte(tc.raw)); actual != "JSON parse error - "+tc.want {
			t.Fatalf("%q: %s", tc.raw, actual)
		}
	}
}

func FuzzJSONDiagnostics(f *testing.F) {
	for _, seed := range []string{`{`, `[1,]`, `{"a" 1}`, `"\uX"`, "\xff", "\xe2\x82"} {
		f.Add([]byte(seed))
	}
	f.Fuzz(func(t *testing.T, data []byte) {
		// Diagnostics must not panic on malformed bytes or nesting. They never
		// decode values or reflect credentials outside the syntax error position.
		_ = JSONParseDetail(data)
		_ = UTF8ParseDetail(data)
	})
}
