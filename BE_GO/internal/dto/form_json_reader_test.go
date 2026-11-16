package dto

import (
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// deepArrays builds a JSON document whose maximum nesting is depth.
func deepArrays(depth int) string { return strings.Repeat("[", depth) + strings.Repeat("]", depth) }

// TestAutomationFormTreeCountsDepth pins auditJSONDepth's D on the shapes the
// measured bands use: [] is 1, [[]] is 2, {"a": {"a": 1}} is 2, and brackets in
// strings or keys do not count.
func TestAutomationFormTreeCountsDepth(t *testing.T) {
	for _, test := range []struct {
		text  string
		depth int
	}{
		{"[]", 1},
		{"[[]]", 2},
		{`{"a": {"a": 1}}`, 2},
		{`[{"deep": [[1]]}]`, 4},
		{`["[[[["]`, 1},
		{`{"[[": 1}`, 1},
		{`"[[[["`, 0},
	} {
		_, ok, _, depth := automationFormTree(test.text)
		if !ok || depth != test.depth {
			t.Fatalf("%s: ok=%v depth=%d, want depth %d", test.text, ok, depth, test.depth)
		}
	}
}

// TestAutomationFormTreeParsesMeasuredBands pins the reader's limit (owner
// decision X15): every measured depth parses, including CPython's own C
// recursion boundary and the documented 10000-level floor.
func TestAutomationFormTreeParsesMeasuredBands(t *testing.T) {
	for _, depth := range []int{600, 9928, 9934, 9937, 9938, 9941, 9942, 9943, 10000} {
		text := deepArrays(depth)
		tree, ok, rejected, got := automationFormTree(text)
		if !ok || rejected || got != depth {
			t.Fatalf("depth %d: ok=%v rejected=%v depth=%d", depth, ok, rejected, got)
		}
		if _, isList := tree.([]any); !isList {
			t.Fatalf("depth %d: parsed %T, want a list", depth, tree)
		}
	}
}

// TestAutomationFormTreeKeepsPythonSemantics pins the value semantics the
// measured bands must preserve: lone surrogate escapes survive as code units
// (the callers' jsonb state) and json.Number keeps integer text.
func TestAutomationFormTreeKeepsPythonSemantics(t *testing.T) {
	tree, ok, rejected, _ := automationFormTree(`[12345678901234567890, "\ud800"]`)
	if !ok || !rejected {
		t.Fatalf("ok=%v rejected=%v", ok, rejected)
	}
	if got := string(PythonJSONDumps(tree)); got != `[12345678901234567890, "\ud800"]` {
		t.Fatalf("dumps %s", got)
	}
	value, ok, _, _ := automationFormTree(`18500000000000000001`)
	if !ok {
		t.Fatal("integer not parsed")
	}
	if text, isNumber := value.(interface{ String() string }); !isNumber || text.String() != "18500000000000000001" {
		t.Fatalf("integer %#v", value)
	}
}

// TestAutomationFormTreePastTheReaderLimit keeps the depth of a document the
// reader refuses: the callers answer their measured band without storing it.
func TestAutomationFormTreePastTheReaderLimit(t *testing.T) {
	text := deepArrays(formJSONNesting + 2)
	tree, ok, rejected, depth := automationFormTree(text)
	if !ok || rejected || depth != formJSONNesting+2 {
		t.Fatalf("ok=%v rejected=%v depth=%d", ok, rejected, depth)
	}
	if list, isList := tree.([]any); !isList || len(list) != 0 {
		t.Fatalf("parsed %T with %d items, want the empty list the callers never store", tree, len(list))
	}
}

// TestValidateNestedPaymentMeasuredFormJSONBand pins the invoice supplier
// update boundary (X15): D = 9941 is an ordinary value, D = 9942 is the
// RecursionError band reported as notPorted, before any later field.
func TestValidateNestedPaymentMeasuredFormJSONBand(t *testing.T) {
	field := func(depth int) domain.RequestObject {
		return domain.RequestObject{{Name: "course", Value: deepArrays(depth)}}
	}
	for _, test := range []struct {
		depth     int
		notPorted bool
	}{
		{9941, false},
		{9942, true},
		{10000, true},
	} {
		got, notPorted, err := ValidateNestedPayment(field(test.depth), true, true, NestedPaymentLookups{FormJSONDepth: 9942})
		if err != nil || notPorted != test.notPorted {
			t.Fatalf("depth %d: err=%v notPorted=%v, want %v", test.depth, err, notPorted, test.notPorted)
		}
		if test.notPorted && got != nil {
			t.Fatalf("depth %d: errors %#v", test.depth, got)
		}
	}
}

func TestAutomationFormTreeSyntaxErrorBeforeDeepTail(t *testing.T) {
	// A syntax error ahead of the deep part: json.loads raises JSONDecodeError
	// before it recurses that far.
	text := "[1,]" + strings.Repeat("[", 12000) + strings.Repeat("]", 12000)
	_, ok, _, depth := automationFormTree(text)
	if ok || depth > 1 {
		t.Fatalf("got ok=%v depth=%d, want the invalid-JSON answer at depth 1", ok, depth)
	}
	// Unclosed deep nesting: Python recurses past the limit before the end.
	_, ok, _, depth = automationFormTree(strings.Repeat("[", 12000))
	if ok || depth != 12000 {
		t.Fatalf("got ok=%v depth=%d, want depth 12000", ok, depth)
	}
}
