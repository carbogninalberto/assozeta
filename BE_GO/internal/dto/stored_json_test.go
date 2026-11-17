package dto

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

func storedText(value string) *string { return &value }

// testStoredJSONEdge is a nesting bound the tests can author quickly.
const testStoredJSONEdge = 5000

func TestStoredJSONValues(t *testing.T) {
	if value, err := StoredJSONEdge(nil, testStoredJSONEdge); value != nil || err != nil {
		t.Fatalf("NULL: %v %v", value, err)
	}
	value, err := StoredJSONEdge(storedText(`{"a": [1, -2, 1.5, true, null], "b": {"c": "d"}}`), testStoredJSONEdge)
	if err != nil {
		t.Fatal(err)
	}
	object := value.(domain.RequestObject)
	first := object[0].Value.([]any)
	if len(object) != 2 || first[0] != json.Number("1") || first[1] != json.Number("-2") || first[2] != json.Number("1.5") ||
		first[3] != true || first[4] != nil {
		t.Fatalf("value %#v", value)
	}
	if nested := object[1].Value.(domain.RequestObject); nested[0].Name != "c" || nested[0].Value != "d" {
		t.Fatalf("nested %#v", nested)
	}
}

// Python keeps a duplicate key's last value at its first position.
func TestStoredJSONDuplicateKeys(t *testing.T) {
	value, err := StoredJSONEdge(storedText(`{"a": 1, "b": 2, "a": 3}`), testStoredJSONEdge)
	if err != nil {
		t.Fatal(err)
	}
	object := value.(domain.RequestObject)
	if len(object) != 2 || object[0].Name != "a" || object[0].Value != json.Number("3") || object[1].Name != "b" {
		t.Fatalf("object %#v", object)
	}
}

// Shallow stored JSON is what the association import writes and every Django
// read parses: 600 and 2000 levels are reproduced (the old 500-level audit
// limit is the audit serializer's own, not this view's). At the call site's
// measured edge it still parses.
func TestStoredJSONNestingIsReproduced(t *testing.T) {
	for _, depth := range []int{1, 600, 2000, testStoredJSONEdge - 1, testStoredJSONEdge} {
		text := strings.Repeat(`[`, depth) + strings.Repeat(`]`, depth)
		value, err := StoredJSONEdge(storedText(text), testStoredJSONEdge)
		if err != nil {
			t.Fatalf("depth %d: %v", depth, err)
		}
		count := 0
		for current := value; ; {
			items, ok := current.([]any)
			if !ok {
				break
			}
			count++
			if len(items) == 0 {
				break
			}
			current = items[0]
		}
		if count != depth {
			t.Fatalf("depth %d walked %d", depth, count)
		}
	}
}

// json.loads raises RecursionError one level past the call site's measured
// edge (owner decision X15). The text names the first container entered past
// the edge, in document order.
func TestStoredJSONRecursionEdge(t *testing.T) {
	nested := func(open, close string, depth int) *string {
		return storedText(strings.Repeat(open, depth) + strings.Repeat(close, depth))
	}
	for _, c := range []struct {
		text   *string
		object bool
	}{
		{nested("[", "]", testStoredJSONEdge+1), false},
		{nested("{", "}", testStoredJSONEdge+1), true},
		{storedText(`{"a": ` + strings.Repeat("[", testStoredJSONEdge) + strings.Repeat("]", testStoredJSONEdge) + `}`), false},
		{storedText(`[` + strings.Repeat(`{"a":`, testStoredJSONEdge) + `1` + strings.Repeat("}", testStoredJSONEdge) + `]`), true},
	} {
		_, err := StoredJSONEdge(c.text, testStoredJSONEdge)
		var recursion *StoredJSONRecursionError
		if !errors.As(err, &recursion) || !errors.Is(err, ErrStoredJSONRecursion) || recursion.Object != c.object {
			t.Fatalf("past the edge: %v", err)
		}
		kind := "array"
		if c.object {
			kind = "object"
		}
		want := "maximum recursion depth exceeded while decoding a JSON " + kind + " from a unicode string"
		if err.Error() != want {
			t.Fatalf("text %q, want %q", err.Error(), want)
		}
	}
}

// PythonJSONLoadsEdge is the same bound for JSON text from a request or a ZIP
// member.
func TestPythonJSONLoadsEdge(t *testing.T) {
	for _, depth := range []int{1, 600, testStoredJSONEdge - 1, testStoredJSONEdge} {
		text := strings.Repeat(`[`, depth) + strings.Repeat(`]`, depth)
		if _, err := PythonJSONLoadsEdge([]byte(text), testStoredJSONEdge); err != nil {
			t.Fatalf("depth %d: %v", depth, err)
		}
	}
	_, err := PythonJSONLoadsEdge([]byte(`{"a": `+strings.Repeat("[", testStoredJSONEdge)+strings.Repeat("]", testStoredJSONEdge)+`}`),
		testStoredJSONEdge)
	var recursion *StoredJSONRecursionError
	if !errors.As(err, &recursion) || recursion.Object || !errors.Is(err, ErrStoredJSONRecursion) {
		t.Fatalf("past the edge: %v", err)
	}
	_, err = PythonJSONLoadsEdge([]byte(strings.Repeat(`{"a":`, testStoredJSONEdge+1)+`1`+strings.Repeat("}", testStoredJSONEdge+1)),
		testStoredJSONEdge)
	if !errors.As(err, &recursion) || !recursion.Object {
		t.Fatalf("object past the edge: %v", err)
	}
}

// CPython converts an integer literal through int(str), which raises ValueError
// past sys.int_max_str_digits (4300); float literals go through strtod.
func TestStoredJSONIntegerDigits(t *testing.T) {
	if _, err := StoredJSONEdge(storedText(`[`+strings.Repeat(`1`, StoredJSONIntegerDigits)+`]`), testStoredJSONEdge); err != nil {
		t.Fatalf("4300 digits: %v", err)
	}
	if _, err := StoredJSONEdge(storedText(`[`+strings.Repeat(`1`, StoredJSONIntegerDigits+1)+`]`), testStoredJSONEdge); !errors.Is(err, ErrStoredJSONInteger) {
		t.Fatalf("4301 digits: %v", err)
	}
	if _, err := StoredJSONEdge(storedText(`[`+strings.Repeat(`9`, StoredJSONIntegerDigits+1)+`.0]`), testStoredJSONEdge); err != nil {
		t.Fatalf("float digits: %v", err)
	}
	if _, err := StoredJSONEdge(storedText(`[1e400]`), testStoredJSONEdge); err != nil {
		t.Fatalf("exponent: %v", err)
	}
}

// jsonb stores neither a lone surrogate escape nor the NaN/Infinity literals.
func TestStoredJSONRejectsWhatJSONBCannotStore(t *testing.T) {
	for _, text := range []string{`["\ud800"]`, `["\udc00"]`, `[NaN]`, `[Infinity]`, `[-Infinity]`} {
		_, err := StoredJSONEdge(storedText(text), testStoredJSONEdge)
		if !errors.Is(err, refusal.ErrAppState) || errors.Is(err, refusal.ErrNotPorted) || !errors.Is(err, ErrStoredJSONUnreachable) {
			t.Fatalf("%s: %v", text, err)
		}
	}
	if value, err := StoredJSONEdge(storedText(`["\ud83d\ude00"]`), testStoredJSONEdge); err != nil || value.([]any)[0] != "😀" {
		t.Fatalf("surrogate pair: %v %v", value, err)
	}
}
