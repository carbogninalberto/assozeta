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

func TestStoredJSONValues(t *testing.T) {
	if value, err := StoredJSON(nil); value != nil || err != nil {
		t.Fatalf("NULL: %v %v", value, err)
	}
	value, err := StoredJSON(storedText(`{"a": [1, -2, 1.5, true, null], "b": {"c": "d"}}`))
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
	value, err := StoredJSON(storedText(`{"a": 1, "b": 2, "a": 3}`))
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
// limit is the audit serializer's own, not this view's).
func TestStoredJSONNestingIsReproduced(t *testing.T) {
	for _, depth := range []int{1, 600, 2000, StoredJSONRecursionBand - 1, StoredJSONRecursionBand} {
		text := strings.Repeat(`[`, depth) + strings.Repeat(`]`, depth)
		value, err := StoredJSON(storedText(text))
		if depth == StoredJSONRecursionBand {
			if !errors.Is(err, ErrJSONRecursionBand) || !errors.Is(err, refusal.ErrNotPorted) {
				t.Fatalf("depth %d: %v", depth, err)
			}
			continue
		}
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

// json.loads raises RecursionError at 9999 levels on every call stack; the
// 9800..9998 band depends on the C calls already on the request thread's stack.
func TestStoredJSONRecursion(t *testing.T) {
	band := strings.Repeat(`[`, StoredJSONRecursionFloor-1) + strings.Repeat(`]`, StoredJSONRecursionFloor-1)
	if _, err := StoredJSON(storedText(band)); !errors.Is(err, ErrJSONRecursionBand) {
		t.Fatalf("9998: %v", err)
	}
	floor := strings.Repeat(`[`, StoredJSONRecursionFloor) + strings.Repeat(`]`, StoredJSONRecursionFloor)
	if _, err := StoredJSON(storedText(floor)); !errors.Is(err, ErrStoredJSONRecursion) {
		t.Fatalf("9999: %v", err)
	}
}

// CPython converts an integer literal through int(str), which raises ValueError
// past sys.int_max_str_digits (4300); float literals go through strtod.
func TestStoredJSONIntegerDigits(t *testing.T) {
	if _, err := StoredJSON(storedText(`[` + strings.Repeat(`1`, StoredJSONIntegerDigits) + `]`)); err != nil {
		t.Fatalf("4300 digits: %v", err)
	}
	if _, err := StoredJSON(storedText(`[` + strings.Repeat(`1`, StoredJSONIntegerDigits+1) + `]`)); !errors.Is(err, ErrStoredJSONInteger) {
		t.Fatalf("4301 digits: %v", err)
	}
	if _, err := StoredJSON(storedText(`[` + strings.Repeat(`9`, StoredJSONIntegerDigits+1) + `.0]`)); err != nil {
		t.Fatalf("float digits: %v", err)
	}
	if _, err := StoredJSON(storedText(`[1e400]`)); err != nil {
		t.Fatalf("exponent: %v", err)
	}
}

// jsonb stores neither a lone surrogate escape nor the NaN/Infinity literals.
func TestStoredJSONRejectsWhatJSONBCannotStore(t *testing.T) {
	for _, text := range []string{`["\ud800"]`, `["\udc00"]`, `[NaN]`, `[Infinity]`, `[-Infinity]`} {
		_, err := StoredJSON(storedText(text))
		if !errors.Is(err, refusal.ErrAppState) || errors.Is(err, refusal.ErrNotPorted) || !errors.Is(err, ErrStoredJSONUnreachable) {
			t.Fatalf("%s: %v", text, err)
		}
	}
	if value, err := StoredJSON(storedText(`["\ud83d\ude00"]`)); err != nil || value.([]any)[0] != "😀" {
		t.Fatalf("surrogate pair: %v %v", value, err)
	}
}
