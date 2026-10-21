package dto

import "testing"

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
