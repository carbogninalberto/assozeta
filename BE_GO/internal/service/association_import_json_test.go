package service

import (
	"encoding/json"
	"errors"
	"math"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// Expected values in this file were produced by CPython 3.13.16 running
// json.loads / application.services.json_stream.iter_json_array.

func TestImportJSONParseErrors(t *testing.T) {
	cases := []struct{ input, want string }{
		{``, "Expecting value: line 1 column 1 (char 0)"},
		{` `, "Expecting value: line 1 column 2 (char 1)"},
		{`{`, "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"},
		{`[`, "Expecting value: line 1 column 2 (char 1)"},
		{`{}x`, "Extra data: line 1 column 3 (char 2)"},
		{`{}}`, "Extra data: line 1 column 3 (char 2)"},
		{`{"a"}`, "Expecting ':' delimiter: line 1 column 5 (char 4)"},
		{`{"a":}`, "Expecting value: line 1 column 6 (char 5)"},
		{`{"a":1,}`, "Illegal trailing comma before end of object: line 1 column 7 (char 6)"},
		{`[1,]`, "Illegal trailing comma before end of array: line 1 column 3 (char 2)"},
		{`[1 , ]`, "Illegal trailing comma before end of array: line 1 column 4 (char 3)"},
		{`[1 2]`, "Expecting ',' delimiter: line 1 column 4 (char 3)"},
		{`{1:2}`, "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"},
		{`{,}`, "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)"},
		{`{"a" 1}`, "Expecting ':' delimiter: line 1 column 6 (char 5)"},
		{`{"a":1,,"b":2}`, "Expecting property name enclosed in double quotes: line 1 column 8 (char 7)"},
		{`nul`, "Expecting value: line 1 column 1 (char 0)"},
		{`tru`, "Expecting value: line 1 column 1 (char 0)"},
		{`nan`, "Expecting value: line 1 column 1 (char 0)"},
		{`-Inf`, "Expecting value: line 1 column 1 (char 0)"},
		{`+1`, "Expecting value: line 1 column 1 (char 0)"},
		{`.5`, "Expecting value: line 1 column 1 (char 0)"},
		{`-.5`, "Expecting value: line 1 column 1 (char 0)"},
		{`-`, "Expecting value: line 1 column 1 (char 0)"},
		{`"abc`, "Unterminated string starting at: line 1 column 1 (char 0)"},
		{`"a\`, "Unterminated string starting at: line 1 column 1 (char 0)"},
		{"\"a\x01b\"", "Invalid control character at: line 1 column 3 (char 2)"},
		{"\"a\nb\"", "Invalid control character at: line 1 column 3 (char 2)"},
		{`"a\qb"`, `Invalid \escape: line 1 column 3 (char 2)`},
		{`"\x"`, `Invalid \escape: line 1 column 2 (char 1)`},
		{`"a\u12"`, `Invalid \uXXXX escape: line 1 column 4 (char 3)`},
		{`"\u12`, `Invalid \uXXXX escape: line 1 column 3 (char 2)`},
		{`00`, "Extra data: line 1 column 2 (char 1)"},
		{`01`, "Extra data: line 1 column 2 (char 1)"},
		{`1.`, "Extra data: line 1 column 2 (char 1)"},
		{`1e`, "Extra data: line 1 column 2 (char 1)"},
		{`1e+`, "Extra data: line 1 column 2 (char 1)"},
		{`1.5e`, "Extra data: line 1 column 4 (char 3)"},
		{`1e5x`, "Extra data: line 1 column 4 (char 3)"},
		{`1.5.5`, "Extra data: line 1 column 4 (char 3)"},
		{`-0`, ""},
		{`-01`, "Extra data: line 1 column 3 (char 2)"},
		{`[01]`, "Expecting ',' delimiter: line 1 column 3 (char 2)"},
		{`[1x]`, "Expecting ',' delimiter: line 1 column 3 (char 2)"},
		{`{"a":1x}`, "Expecting ',' delimiter: line 1 column 7 (char 6)"},
		{`[truee]`, "Expecting ',' delimiter: line 1 column 6 (char 5)"},
		{`[NaNx]`, "Expecting ',' delimiter: line 1 column 5 (char 4)"},
		{"[\n1,\n]", "Illegal trailing comma before end of array: line 2 column 2 (char 3)"},
		{"{\"a\"\n:\n1,\n}", "Illegal trailing comma before end of object: line 3 column 2 (char 8)"},
		{"[\n1,\n2\n]x", "Extra data: line 4 column 2 (char 8)"},
		{"[\n1,\n", "Expecting value: line 3 column 1 (char 5)"},
		{"\n\n  x", "Expecting value: line 3 column 3 (char 4)"},
		{`{"a":1,`, "Expecting property name enclosed in double quotes: line 1 column 8 (char 7)"},
		{`[1,`, "Expecting value: line 1 column 4 (char 3)"},
		{"\xff{}", "'utf-8' codec can't decode byte 0xff in position 0: invalid start byte"},
		{"{\"a\": \"\xff\"}", "'utf-8' codec can't decode byte 0xff in position 7: invalid start byte"},
		{"\xff\xfe\x41", "'utf-16-le' codec can't decode byte 0x41 in position 2: truncated data"},
		{"\xfe\xff\x00", "'utf-16-be' codec can't decode byte 0x00 in position 2: truncated data"},
		{"\x00\x00\x00\x00\x31", "'utf-32-be' codec can't decode byte 0x31 in position 4: truncated data"},
	}
	for _, c := range cases {
		_, err := importJSONParse([]byte(c.input))
		if c.want == "" {
			if err != nil {
				t.Errorf("importJSONParse(%q) = %v, want a value", c.input, err)
			}
			continue
		}
		if err == nil || err.Error() != c.want {
			t.Errorf("importJSONParse(%q) = %v, want %q", c.input, err, c.want)
		}
	}
}

func TestImportJSONParseValues(t *testing.T) {
	value, err := importJSONParse([]byte(`{"a":1,"a":2,"b":[1,2.5,null,true,"x"]}`))
	if err != nil {
		t.Fatal(err)
	}
	object, ok := value.(domain.RequestObject)
	if !ok {
		t.Fatalf("unexpected %T", value)
	}
	if got := importLookup(object, "a"); got != json.Number("2") {
		t.Errorf("duplicate key keeps the last value, got %#v", got)
	}
	if got := importJSONStr(importLookup(object, "b")); got != "[1, 2.5, None, True, 'x']" {
		t.Errorf("str(list) = %s", got)
	}
	// The first position of a duplicate key stays (Python dict order).
	keys := []string{}
	for _, field := range object {
		keys = append(keys, field.Name)
	}
	if strings.Join(keys, ",") != "a,b" {
		t.Errorf("order = %v", keys)
	}

	// sys.int_max_str_digits: 4300 digits read, 4301 raise ValueError.
	fourThousandThreeHundred := "1" + strings.Repeat("0", 4299)
	if _, err := importJSONParse([]byte(fourThousandThreeHundred)); err != nil {
		t.Fatalf("4300 digits: %v", err)
	}
	_, err = importJSONParse([]byte("1" + strings.Repeat("0", 4300)))
	want := "Exceeds the limit (4300 digits) for integer string conversion: value has 4301 digits; " +
		"use sys.set_int_max_str_digits() to increase the limit"
	if err == nil || err.Error() != want {
		t.Errorf("4301 digits = %v, want %q", err, want)
	}

	// Python values the standard decoder rejects: NaN/Infinity literals, a
	// lone surrogate escape and a float beyond float64.
	nan, err := importJSONParse([]byte(`NaN`))
	if number, ok := nan.(float64); err != nil || !ok || !math.IsNaN(number) {
		t.Errorf("NaN = %#v, %v", nan, err)
	}
	infinite, err := importJSONParse([]byte(`[Infinity,-Infinity,1e999,-123e4567]`))
	if floats, ok := infinite.([]any); err != nil || !ok || len(floats) != 4 ||
		!math.IsInf(floats[0].(float64), 1) || !math.IsInf(floats[1].(float64), -1) ||
		!math.IsInf(floats[2].(float64), 1) || !math.IsInf(floats[3].(float64), -1) {
		t.Errorf("infinities = %#v, %v", infinite, err)
	}
	surrogate, err := importJSONParse([]byte(`"\ud800"`))
	if text, ok := surrogate.(string); err != nil || !ok || !importTextHasSurrogate(text) {
		t.Errorf("lone surrogate = %#v, %v", surrogate, err)
	}
	pair, err := importJSONParse([]byte(`"\ud83d\ude00"`))
	if text, ok := pair.(string); err != nil || !ok || text != "\U0001F600" {
		t.Errorf("surrogate pair = %#v, %v", pair, err)
	}

	// json.detect_encoding: the same document in UTF-8 with a BOM, UTF-16
	// (both orders, with a BOM) and UTF-32 (both orders, with a BOM).
	document := `{"a": "è"}`
	for name, encoded := range map[string][]byte{
		"utf-8-sig": append([]byte{0xEF, 0xBB, 0xBF}, document...),
		"utf-16-le": append([]byte{0xFF, 0xFE}, importUTF16(t, document, true)...),
		"utf-16-be": append([]byte{0xFE, 0xFF}, importUTF16(t, document, false)...),
		"utf-32-le": append([]byte{0xFF, 0xFE, 0x00, 0x00}, importUTF32(t, document, true)...),
		"utf-32-be": append([]byte{0x00, 0x00, 0xFE, 0xFF}, importUTF32(t, document, false)...),
	} {
		value, err := importJSONParse(encoded)
		if err != nil {
			t.Errorf("%s: %v", name, err)
			continue
		}
		object, ok := value.(domain.RequestObject)
		if !ok {
			t.Errorf("%s: %T", name, value)
			continue
		}
		if got := importJSONStr(importLookup(object, "a")); got != "è" {
			t.Errorf("%s = %s", name, got)
		}
	}
}

func importUTF16(t *testing.T, text string, little bool) []byte {
	t.Helper()
	var out []byte
	for _, r := range text {
		if r > 0xFFFF {
			t.Fatalf("not a BMP character: %q", r)
		}
		if little {
			out = append(out, byte(r), byte(r>>8))
		} else {
			out = append(out, byte(r>>8), byte(r))
		}
	}
	return out
}

func importUTF32(t *testing.T, text string, little bool) []byte {
	t.Helper()
	var out []byte
	for _, r := range text {
		if little {
			out = append(out, byte(r), byte(r>>8), 0, 0)
		} else {
			out = append(out, 0, 0, byte(r>>8), byte(r))
		}
	}
	return out
}

func TestImportIterJSONArray(t *testing.T) {
	cases := []struct {
		input   string
		want    int
		wantErr string
	}{
		{``, 0, "Expected a JSON array"},
		{` `, 0, "Expected a JSON array"},
		{`[]`, 0, ""},
		{`[ ]`, 0, ""},
		{`[1,2]`, 2, ""},
		{`[1,]`, 1, "Expecting value"},
		{`[,]`, 0, "Expecting value"},
		{`[1,,2]`, 1, "Expecting value"},
		{`[`, 0, "Unterminated JSON array"},
		{`[1`, 1, "Unterminated JSON array"},
		{`[1,`, 1, "Unterminated JSON array"},
		{`{"a":1}`, 0, "Expected a JSON array"},
		{`1`, 0, "Expected a JSON array"},
		{`[1]x`, 1, "Extra data after JSON array"},
		{`[1] `, 1, ""},
		{`[1 2]`, 1, "Expecting ',' delimiter"},
		{`[01]`, 0, "Expecting ',' delimiter"},
		{`[1.]`, 0, "Expecting ',' delimiter"},
		{`[1e]`, 0, "Expecting ',' delimiter"},
		{`["abc]`, 0, "Unterminated string starting at: line 1 column 2 (char 1)"},
		{"[\"a\x01\"]", 0, "Invalid control character at: line 1 column 4 (char 3)"},
		{`["\q"]`, 0, `Invalid \escape: line 1 column 3 (char 2)`},
		{"[1]\x00", 1, "Extra data after JSON array"},
		{"\xef\xbb\xbf[1]", 1, ""},
		{"\xff[1]", 0, "'utf-8' codec can't decode byte 0xff in position 0: invalid start byte"},
		{`[1,2,3]  `, 3, ""},
		{`[--1]`, 0, "Expecting value: line 1 column 2 (char 1)"},
	}
	for _, c := range cases {
		items := []any{}
		err := importIterJSONArray(&importBytesReader{data: []byte(c.input)}, importJSONChunkSize, importValidationJSONEdge, func(item any) error {
			items = append(items, item)
			return nil
		})
		if c.wantErr == "" {
			if err != nil || len(items) != c.want {
				t.Errorf("iter(%q) = %v, %v, want %d items", c.input, items, err, c.want)
			}
			continue
		}
		if err == nil || err.Error() != c.wantErr || len(items) != c.want {
			t.Errorf("iter(%q) = %v, %v; want %d items and %q", c.input, items, err, c.want, c.wantErr)
		}
	}
}

// TestImportIterJSONArrayChunks checks that a chunk boundary never changes
// what the stream yields or which error it raises.
func TestImportIterJSONArrayChunks(t *testing.T) {
	cases := []struct {
		input   string
		want    []string
		wantErr string
	}{
		{`[1,2,3]`, []string{"1", "2", "3"}, ""},
		{`[123456, 7]`, []string{"123456", "7"}, ""},
		{`[1e5]`, []string{"100000.0"}, ""},
		{`["abcdef"]`, []string{"abcdef"}, ""},
		{`[1.]`, nil, "Expecting ',' delimiter"},
		{`[{"user_id": 12}, {"a":1}]`, []string{"{'user_id': 12}", "{'a': 1}"}, ""},
	}
	for _, chunk := range []int{1, 2, 3, 4, 5, 7} {
		for _, c := range cases {
			got := []string{}
			err := importIterJSONArray(&importBytesReader{data: []byte(c.input)}, chunk, importValidationJSONEdge, func(item any) error {
				got = append(got, importJSONStr(item))
				return nil
			})
			if err != nil {
				if c.wantErr == "" || err.Error() != c.wantErr {
					t.Errorf("%q at chunk %d: %v, want %q", c.input, chunk, err, c.wantErr)
				}
				continue
			}
			if c.wantErr != "" || strings.Join(got, ",") != strings.Join(c.want, ",") {
				t.Errorf("%q at chunk %d = %v, want %v (%q)", c.input, chunk, got, c.want, c.wantErr)
			}
		}
	}
}

// TestImportJSONRecursionBounds: json.loads parses up to the measured edge of
// POST association/import/validate (owner decision X15) and raises
// RecursionError one level past it. The text names the container entered past
// the edge.
func TestImportJSONRecursionBounds(t *testing.T) {
	nested := func(open, close string, n int) []byte {
		return []byte(strings.Repeat(open, n) + strings.Repeat(close, n))
	}
	for _, n := range []int{importValidationJSONEdge - 1, importValidationJSONEdge} {
		if _, err := importJSONParse(nested("[", "]", n)); err != nil {
			t.Fatalf("%d levels: %v", n, err)
		}
	}
	for _, c := range []struct {
		text   []byte
		object bool
	}{
		{nested("[", "]", importValidationJSONEdge+1), false},
		{nested(`{"a":`, "}", importValidationJSONEdge+1), true},
	} {
		_, err := importJSONParse(c.text)
		var recursion *importJSONRecursionError
		if !errors.As(err, &recursion) || recursion.object != c.object {
			t.Fatalf("past the edge: %v", err)
		}
		kind := "array"
		if c.object {
			kind = "object"
		}
		want := "maximum recursion depth exceeded while decoding a JSON " + kind + " from a unicode string"
		if err.Error() != want {
			t.Fatalf("%d levels: %q, want %q", importValidationJSONEdge+1, err.Error(), want)
		}
	}
}

// TestImportIterJSONArrayRecursion: each item's own nesting counts, and a
// RecursionError is not retried after a fill.
func TestImportIterJSONArrayRecursion(t *testing.T) {
	item := func(depth int) string { return strings.Repeat(`[`, depth) + strings.Repeat(`]`, depth) }
	for _, depth := range []int{importValidationJSONEdge - 1, importValidationJSONEdge} {
		input := "[" + item(depth) + "]"
		err := importIterJSONArray(&importBytesReader{data: []byte(input)}, 7, importValidationJSONEdge, func(any) error { return nil })
		if err != nil {
			t.Fatalf("item depth %d: %v", depth, err)
		}
	}
	input := "[" + item(importValidationJSONEdge+1) + "]"
	err := importIterJSONArray(&importBytesReader{data: []byte(input)}, 7, importValidationJSONEdge, func(any) error { return nil })
	var recursion *importJSONRecursionError
	if !errors.As(err, &recursion) || recursion.object {
		t.Fatalf("item past the edge: %v", err)
	}
}
