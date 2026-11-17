package dto

import (
	"fmt"
	"math/rand"
	"strings"
	"testing"
)

// TestPythonJSONLoadsDepth pins the nesting json.loads reaches before it stops:
// the first error, or the end of the first value. Whatever follows is "Extra
// data" (or never reached), so a deep tail after it adds nothing.
func TestPythonJSONLoadsDepth(t *testing.T) {
	deep := strings.Repeat("[", 30000) + strings.Repeat("]", 30000)
	for _, test := range []struct {
		name string
		text string
		want int
	}{
		{"extra-data-after-empty-object", "{} " + deep, 1},
		{"extra-data-after-array", "[[1]] x", 2},
		{"int-digit-limit-before-deep-tail", "[" + strings.Repeat("1", 5000) + ", " + deep + "]", 1},
		{"bad-escape-before-deep-tail", `["\uZZZZ", ` + deep + "]", 1},
		{"deep-array", "[" + deep + "]", 30001},
		{"bad-escape-after-deep-array", "[" + deep + `, "\uZZZZ"]`, 30001},
		{"fraction-without-digit", "[1., [[]]]", 1},
		{"leading-zero", "[01, [[]]]", 1},
		{"exponent-without-digit", "[1e, [[]]]", 1},
		{"number-then-nested", "[1.5e+3, [[]]]", 3},
		{"object-trailing-comma", `{"a":1,}`, 1},
		{"array-trailing-comma-deep-tail", `{"a":[1,],"b":[[[]]]}`, 2},
		{"python-constants", "[NaN, [-Infinity, [Infinity]]]", 3},
		{"truncated-null", "[nul, [[]]]", 1},
		{"utf8-bom", "\ufeff[[]]", 0},
		{"surrounding-whitespace", "   [[ ]]  ", 2},
		{"control-byte-in-string", "[\"a\x01\", [[]]]", 1},
		{"escaped-backslash", `["\\", [[]]]`, 3},
		{"brackets-in-key", `{"[[[": [1]}`, 2},
		{"4300-digit-int", "[" + strings.Repeat("1", 4300) + ", [[]]]", 3},
		{"4301-digit-negative-int", "[-" + strings.Repeat("1", 4301) + ", [[]]]", 1},
		{"empty", "", 0},
		{"unterminated-deep-array", "[[[", 3},
		{"missing-colon", `{"a" 1, "b": [[]]}`, 1},
		{"missing-comma", "[1 2, [[]]]", 1},
	} {
		t.Run(test.name, func(t *testing.T) {
			if got := pythonJSONLoadsDepth(test.text); got != test.want {
				t.Fatalf("depth = %d, want %d", got, test.want)
			}
		})
	}
}

// TestPythonJSONLoadsDepthMatchesAuditJSONDepth is the parity property: on any
// valid document (one value, optional whitespace) both readers agree.
func TestPythonJSONLoadsDepthMatchesAuditJSONDepth(t *testing.T) {
	deep := strings.Repeat("[", 30000) + strings.Repeat("]", 30000)
	documents := []string{
		"{}", "[]", "[[1]]", `{"a": 1}`, `[1, 2, [3, {"a": [[4]]}]]`,
		`"a"`, `"[[[["`, "1", "-1.5e+3", "null", "true", "false",
		"NaN", "Infinity", "-Infinity", "[NaN, [-Infinity, [Infinity]]]",
		"   [[ ]]  ", `{"[[[": [1]}`, `["\\", [[]]]`, deep, "[" + deep + "]",
		`["\u0041", {"a": []}]`, `["\ud83d\ude00"]`, `[1.5e+3, [[]]]`,
		`{"a": [1], "b": [[[]]]}`, "[" + strings.Repeat("1", 4300) + "]",
	}
	r := rand.New(rand.NewSource(1))
	for i := 0; i < 50; i++ {
		documents = append(documents, randomJSONDocument(r, 4))
	}
	for _, text := range documents {
		if got, want := pythonJSONLoadsDepth(text), auditJSONDepth(text); got != want {
			t.Fatalf("%q: depth = %d, auditJSONDepth = %d", text, got, want)
		}
	}
}

// randomJSONDocument builds a small valid JSON document (numbers, strings,
// arrays and objects) to compare the two depth readers on shapes no table row
// spells out.
func randomJSONDocument(r *rand.Rand, depth int) string { return randomJSONValue(r, depth) }

func randomJSONValue(r *rand.Rand, depth int) string {
	scalars := []string{"0", "-12", "1.5e+3", `"s"`, `"[[["`, "true", "null"}
	if depth <= 0 || r.Intn(3) == 0 {
		return scalars[r.Intn(len(scalars))]
	}
	items := make([]string, 1+r.Intn(3))
	for i := range items {
		items[i] = randomJSONValue(r, depth-1)
	}
	if r.Intn(2) == 0 {
		return "[" + strings.Join(items, ", ") + "]"
	}
	fields := make([]string, len(items))
	for i := range items {
		fields[i] = fmt.Sprintf(`"k%d": %s`, i, items[i])
	}
	return "{" + strings.Join(fields, ", ") + "}"
}
