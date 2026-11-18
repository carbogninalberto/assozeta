package importeffects

import (
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// TestPyreprCorpus compares str(), repr() and json.dumps(sort_keys=True) with
// CPython 3.13 for decoded JSON values (the expected strings were printed by
// CPython; json is the archive member text).
func TestPyreprCorpus(t *testing.T) {
	cases := []struct{ json, text, repr, dumps string }{
		{"{\"a\": 1}", "{'a': 1}", "{'a': 1}", "{\"a\": 1}"},
		{"[1, 2.5, null, true]", "[1, 2.5, None, True]", "[1, 2.5, None, True]", "[1, 2.5, null, true]"},
		{"{\"a\": {\"b\": [1, {\"c\": \"d\"}]}}", "{'a': {'b': [1, {'c': 'd'}]}}", "{'a': {'b': [1, {'c': 'd'}]}}", "{\"a\": {\"b\": [1, {\"c\": \"d\"}]}}"},
		{"[]", "[]", "[]", "[]"},
		{"{}", "{}", "{}", "{}"},
		{"\"simple\"", "simple", "'simple'", "\"simple\""},
		{"\"q'w\\\"e\"", "q'w\"e", "'q\\'w\"e'", "\"q'w\\\"e\""},
		{"\"back\\\\slash\"", "back\\slash", "'back\\\\slash'", "\"back\\\\slash\""},
		{"\"\\t\\n\\r\\b\\f\\u0000\\u001f\\u007f\"", "\x09\x0a\x0d\x08\x0c\x00\x1f\x7f", "'\\t\\n\\r\\x08\\x0c\\x00\\x1f\\x7f'", "\"\\t\\n\\r\\b\\f\\u0000\\u001f\\u007f\""},
		{"\"\\u00e0\\u00e8\\u00ec\\u00f2\\u00f9\"", "\xc3\xa0\xc3\xa8\xc3\xac\xc3\xb2\xc3\xb9", "'\xc3\xa0\xc3\xa8\xc3\xac\xc3\xb2\xc3\xb9'", "\"\\u00e0\\u00e8\\u00ec\\u00f2\\u00f9\""},
		{"\"\\u00a0\"", "\xc2\xa0", "'\\xa0'", "\"\\u00a0\""},
		{"\"emoji \\ud83d\\ude00\"", "emoji \xf0\x9f\x98\x80", "'emoji \xf0\x9f\x98\x80'", "\"emoji \\ud83d\\ude00\""},
		{"\"combining \\u00e9\"", "combining \xc3\xa9", "'combining \xc3\xa9'", "\"combining \\u00e9\""},
		{"\"\\u2028\\u2029\"", "\xe2\x80\xa8\xe2\x80\xa9", "'\\u2028\\u2029'", "\"\\u2028\\u2029\""},
		{"\"line\\nfeed\"", "line\x0afeed", "'line\\nfeed'", "\"line\\nfeed\""},
		{"{\"name\": \"INFORMATIVA\"}", "{'name': 'INFORMATIVA'}", "{'name': 'INFORMATIVA'}", "{\"name\": \"INFORMATIVA\"}"},
		{"[0.1, 1e+16, 1e-05, -0.0, 1.0]", "[0.1, 1e+16, 1e-05, -0.0, 1.0]", "[0.1, 1e+16, 1e-05, -0.0, 1.0]", "[0.1, 1e+16, 1e-05, -0.0, 1.0]"},
		{"-1e+308", "-1e+308", "-1e+308", "-1e+308"},
		{"123456789012345678", "123456789012345678", "123456789012345678", "123456789012345678"},
		{"10000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000", "10000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000", "10000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000", "10000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"},
		{"{\"k\": \"\\u00e0\"}", "{'k': '\xc3\xa0'}", "{'k': '\xc3\xa0'}", "{\"k\": \"\\u00e0\"}"},
		{"[\"'\", \"\\\"\"]", "[\"'\", '\"']", "[\"'\", '\"']", "[\"'\", \"\\\"\"]"},
		{"{\"x\": -0.0}", "{'x': -0.0}", "{'x': -0.0}", "{\"x\": -0.0}"},
		{"[\"\\b\\u000b\\f\"]", "['\\x08\\x0b\\x0c']", "['\\x08\\x0b\\x0c']", "[\"\\b\\u000b\\f\"]"},
		{"{\"\\u00e0\": \"\\u00e0\"}", "{'\xc3\xa0': '\xc3\xa0'}", "{'\xc3\xa0': '\xc3\xa0'}", "{\"\\u00e0\": \"\\u00e0\"}"},
		{"[true, false, null]", "[True, False, None]", "[True, False, None]", "[true, false, null]"},
		{"[1e-320]", "[1e-320]", "[1e-320]", "[1e-320]"},
		{"[1.7976931348623157e+308]", "[1.7976931348623157e+308]", "[1.7976931348623157e+308]", "[1.7976931348623157e+308]"},
		{"1.5", "1.5", "1.5", "1.5"},
		{"0.0", "0.0", "0.0", "0.0"},
		{"true", "True", "True", "true"},
		{"false", "False", "False", "false"},
		{"null", "None", "None", "null"},
	}
	for _, c := range cases {
		raw, err := dto.ParseProfileTablesJSON([]byte(c.json))
		if err != nil {
			t.Fatalf("decode %s: %v", c.json, err)
		}
		value, err := FromJSON(raw)
		if err != nil {
			t.Fatalf("value %s: %v", c.json, err)
		}
		if got, err := value.Text(); err != nil || got != c.text {
			t.Fatalf("str %s: got %q (%v), want %q", c.json, got, err, c.text)
		}
		if got, err := value.Repr(); err != nil || got != c.repr {
			t.Fatalf("repr %s: got %q (%v), want %q", c.json, got, err, c.repr)
		}
		if got, err := jsonDumpsValue(value, true); err != nil || got != c.dumps {
			t.Fatalf("dumps %s: got %q (%v), want %q", c.json, got, err, c.dumps)
		}
	}
}
