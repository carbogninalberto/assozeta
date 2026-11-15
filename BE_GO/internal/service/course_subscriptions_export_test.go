package service

import (
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// TestCourseSubscriptionExportPythonStrOfParsedJSON pins str() of a parsed
// JSON list and dict against CPython: str([1, 'a', None, True, 1.5]) ==
// "[1, 'a', None, True, 1.5]" and str({'b': 1, 'a': [2]}) ==
// "{'b': 1, 'a': [2]}" (keys in stored order).
func TestCourseSubscriptionExportPythonStrOfParsedJSON(t *testing.T) {
	for _, test := range []struct{ json, want string }{
		{`[1, "a", null, true, 1.5]`, `[1, 'a', None, True, 1.5]`},
		{`{"b": 1, "a": [2]}`, `{'b': 1, 'a': [2]}`},
	} {
		value, err := dto.ParseReferenceJSON([]byte(test.json))
		if err != nil {
			t.Fatalf("%s: %v", test.json, err)
		}
		if got := dto.PythonStr(value); got != test.want {
			t.Errorf("PythonStr(%s) = %q, want %q", test.json, got, test.want)
		}
	}
}

// TestCourseSubscriptionExportLessonsListOrDict is
// f"{lessons_left}/{lessons_total}" with lessons_left stored as a JSON list or
// object: Python writes its repr, dict keys in the stored jsonb order.
func TestCourseSubscriptionExportLessonsListOrDict(t *testing.T) {
	for _, test := range []struct{ meta, want string }{
		{`{"lessons_left": [1, "a", null, true, 1.5]}`, "[1, 'a', None, True, 1.5]/10"},
		{`{"lessons_left": {"b": 1, "a": [2]}}`, "{'b': 1, 'a': [2]}/10"},
		{`{"lessons_left": []}`, "[]/10"},
		{`{"lessons_left": {}}`, "{}/10"},
	} {
		meta := test.meta
		got, err := courseSubscriptionExportLessons(&meta, 10)
		if err != nil {
			t.Fatalf("%s: %v", test.meta, err)
		}
		if got != test.want {
			t.Errorf("%s: got %q, want %q", test.meta, got, test.want)
		}
	}
}
