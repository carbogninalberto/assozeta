package bs4prettify

import (
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"
)

type goldenCase struct {
	Kind   string  `json:"kind"`
	Subset bool    `json:"subset"`
	Input  string  `json:"input"`
	Output *string `json:"output"` // nil: bs4 raised
}

func checkGolden(t *testing.T, cases []goldenCase) (accepted int) {
	t.Helper()
	for i, c := range cases {
		got, err := Prettify(c.Input)
		switch {
		case err == nil && c.Output == nil:
			t.Errorf("%d %s: accepted markup bs4 rejects: %q", i, c.Kind, c.Input)
		case err == nil && got != *c.Output:
			t.Errorf("%d %s: %q\n got %q\nwant %q", i, c.Kind, c.Input, got, *c.Output)
		case err != nil && c.Subset:
			t.Errorf("%d %s: subset input refused: %q", i, c.Kind, c.Input)
		case err != nil && !errors.Is(err, ErrUnsupported):
			t.Errorf("%d %s: unexpected error %v", i, c.Kind, err)
		case err == nil:
			accepted++
		}
	}
	return accepted
}

// TestPrettifyEditorGolden uses bs4 4.13.3 output committed by
// TESTS/adapters/django/bs4_prettify_corpus.py --write-editor.
func TestPrettifyEditorGolden(t *testing.T) {
	raw, err := os.ReadFile("testdata/editor.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []goldenCase
	if err := json.Unmarshal(raw, &cases); err != nil || len(cases) < 90 {
		t.Fatal("editor golden", len(cases), err)
	}
	if accepted := checkGolden(t, cases); accepted != len(cases) {
		t.Fatalf("accepted %d of %d", accepted, len(cases))
	}
}

// fullGoldenCase is one testdata/full.json entry.
type fullGoldenCase struct {
	Kind        string          `json:"kind"`
	Input       pyText          `json:"input"`
	Events      json.RawMessage `json:"events"`
	EventsError *string         `json:"events_error"`
	Tree        json.RawMessage `json:"tree"`
	Output      *pyText         `json:"output"`
	Error       *string         `json:"error"`
}

func readFullGolden(t *testing.T) []fullGoldenCase {
	t.Helper()
	raw, err := os.ReadFile("testdata/full.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []fullGoldenCase
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) < 4000 {
		t.Fatalf("golden corpus has %d cases, want at least 4000", len(cases))
	}
	return cases
}

// resolveGoldenTree replaces {"hex": ...} text values with the byte string.
func resolveGoldenTree(value any) any {
	switch v := value.(type) {
	case map[string]any:
		if text, ok := v["hex"].(string); ok && len(v) == 1 {
			decoded, err := hex.DecodeString(text)
			if err != nil {
				return value
			}
			return string(decoded)
		}
		return value
	case []any:
		for i := range v {
			v[i] = resolveGoldenTree(v[i])
		}
		return v
	}
	return value
}

func decodeGoldenTree(raw json.RawMessage) any {
	var value any
	if err := json.Unmarshal(raw, &value); err != nil {
		return nil
	}
	return resolveGoldenTree(value)
}

// dumpTree is the corpus script's _dump shape: a tag is
// ["tag", name, [[attr, value], ...], [children]], a string is
// ["str", ClassName, text]. It is iterative so deep trees stay safe.
func dumpTree(root *node) any {
	type frame struct {
		n          *node
		child      int
		attrsBuilt bool
		attrs      []any
		children   []any
	}
	frames := []*frame{{n: root}}
	for len(frames) > 0 {
		f := frames[len(frames)-1]
		n := f.n
		if !n.isTag() {
			frames = frames[:len(frames)-1]
			value := []any{"str", n.kind, n.text}
			parent := frames[len(frames)-1]
			parent.children = append(parent.children, value)
			continue
		}
		if !f.attrsBuilt {
			f.attrsBuilt = true
			f.attrs = make([]any, 0, len(n.attrs))
			for _, a := range n.attrs {
				var value any = a.value
				if a.isList {
					list := make([]any, len(a.list))
					for i, item := range a.list {
						list[i] = item
					}
					value = list
				}
				f.attrs = append(f.attrs, []any{a.name, value})
			}
		}
		if f.child < len(n.children) {
			child := n.children[f.child]
			f.child++
			frames = append(frames, &frame{n: child})
			continue
		}
		frames = frames[:len(frames)-1]
		children := f.children
		if children == nil {
			children = []any{}
		}
		value := []any{"tag", n.name, f.attrs, children}
		if len(frames) == 0 {
			if n == root {
				return children
			}
			return value
		}
		parent := frames[len(frames)-1]
		parent.children = append(parent.children, value)
	}
	return nil
}

// TestTreeGolden compares the bs4 tree with the corpus dump. Attributes are in
// insertion order; strings carry their NavigableString subclass name.
func TestTreeGolden(t *testing.T) {
	cases := readFullGolden(t)
	trees, reported, mismatches := 0, 0, 0
	for i, c := range cases {
		if string(c.Tree) == "null" {
			continue
		}
		trees++
		root, err := buildTree(c.Input.value)
		if err != nil {
			mismatches++
			if reported < 25 {
				reported++
				t.Errorf("case %d (%s) input %q: buildTree returned %v", i, c.Kind, c.Input.value, err)
			}
			continue
		}
		got := dumpTree(root)
		want := decodeGoldenTree(c.Tree)
		if !reflect.DeepEqual(got, want) {
			mismatches++
			if reported < 25 {
				reported++
				gotJSON, _ := json.Marshal(got)
				wantJSON, _ := json.Marshal(want)
				t.Errorf("case %d (%s) input %q:\n got %s\nwant %s", i, c.Kind, c.Input.value, gotJSON, wantJSON)
			}
		}
	}
	if mismatches > 0 {
		t.Fatalf("%d of %d trees mismatch", mismatches, trees)
	}
	t.Logf("%d trees match", trees)
}

// TestPrettifyFullGolden requires Prettify to match bs4 for every corpus case.
func TestPrettifyFullGolden(t *testing.T) {
	cases := readFullGolden(t)
	reported, mismatches := 0, 0
	for i, c := range cases {
		got, err := Prettify(c.Input.value)
		problem := ""
		switch {
		case c.Output == nil && errors.Is(err, ErrPythonRaises):
			// bs4 raises, this port reports it.
		case c.Output == nil && err == nil:
			problem = fmt.Sprintf("got %q, want ErrPythonRaises", got)
		case c.Output == nil:
			problem = fmt.Sprintf("got error %v, want ErrPythonRaises", err)
		case errors.Is(err, ErrUnsupported):
			problem = "ErrUnsupported"
		case err != nil:
			problem = fmt.Sprintf("got error %v, want %q", err, c.Output.value)
		case got != c.Output.value:
			problem = fmt.Sprintf("\n got %q\nwant %q", got, c.Output.value)
		}
		if problem == "" {
			continue
		}
		mismatches++
		if reported < 25 {
			reported++
			t.Errorf("case %d (%s) input %q: %s", i, c.Kind, c.Input.value, problem)
		}
	}
	if mismatches > 0 {
		t.Fatalf("%d of %d cases mismatch", mismatches, len(cases))
	}
	t.Logf("%d prettify cases match", len(cases))
}

// TestPrettifyOutputBound checks that deep nesting stops at the output limit
// quickly, while moderate nesting is rendered.
func TestPrettifyOutputBound(t *testing.T) {
	start := time.Now()
	if _, err := Prettify(strings.Repeat("<div>", 6000)); !errors.Is(err, ErrUnsupported) {
		t.Fatalf("deep nesting: %v", err)
	}
	if elapsed := time.Since(start); elapsed > 2*time.Second {
		t.Fatalf("deep nesting took %v", elapsed)
	}
	if _, err := Prettify(strings.Repeat("<div>", 500)); err != nil {
		t.Fatalf("500 nested divs: %v", err)
	}
}

// TestPrettifyRefusesOutsideSubset used to describe the old subset. The same
// inputs now have their real bs4 4.13.3 output; the lone surrogate raises.
func TestPrettifyRefusesOutsideSubset(t *testing.T) {
	cases := []struct {
		input string
		want  string
	}{{
		"<!-- c -->", "<!-- c -->\n",
	}, {
		"<!DOCTYPE html>", "<!DOCTYPE html>\n",
	}, {
		"<?x?>", "<?x?>\n",
	}, {
		"<script>x</script>", "<script>\n x\n</script>\n",
	}, {
		"<style>a</style>", "<style>\n a\n</style>\n",
	}, {
		"<textarea>a</textarea>", "<textarea>a</textarea>\n",
	}, {
		"<title>a</title>", "<title>\n a\n</title>\n",
	}, {
		"a < b", "a &lt; b\n",
	}, {
		"a &b", "a &amp;b\n",
	}, {
		"&amp", "&amp;\n",
	}, {
		"&foo;", "&amp;foo\n",
	}, {
		"&#0;", "\x00\n",
	}, {
		"&#128;", "€\n",
	}, {
		"&#xD800;", "\xed\xa0\x80\n",
	}, {
		"<p class=x>", "<p class=\"x\">\n</p>\n",
	}, {
		"<p a = \"b\">", "<p a=\"b\">\n</p>\n",
	}, {
		"<p a=\"b\"c=\"d\">", "<p a=\"b\" c=\"d\">\n</p>\n",
	}, {
		"</br x>", "",
	}, {
		"<p", "",
	}, {
		"x\x00", "x\x00\n",
	}, {
		"x\x0b", "x\n",
	}, {
		"\u0085", "",
	}, {
		"<p/ >", "<p>\n</p>\n",
	}, {
		"<custom>", "<custom>\n</custom>\n",
	}, {
		"</>", "",
	}, {
		"<!x>", "<!--x-->\n",
	}}
	for _, c := range cases {
		got, err := Prettify(c.input)
		if err != nil || got != c.want {
			t.Errorf("%q: %q %v, want %q", c.input, got, err, c.want)
		}
	}
	if _, err := Prettify("\xed\xa0\x80"); !errors.Is(err, ErrPythonRaises) {
		t.Errorf("lone surrogate: %v", err)
	}
}

func TestPythonSpace(t *testing.T) {
	// str.isspace() for every BMP code point outside the C0/C1 controls the parser refuses.
	want := map[rune]bool{' ': true, '\t': true, '\n': true, '\r': true, '\f': true, '\v': true, 0xa0: true, 0x1680: true,
		0x2028: true, 0x2029: true, 0x202f: true, 0x205f: true, 0x3000: true, 0x85: true, 0x1c: true, 0x1d: true, 0x1e: true, 0x1f: true}
	for r := rune(0x2000); r <= 0x200a; r++ {
		want[r] = true
	}
	for r := rune(0); r <= 0xffff; r++ {
		if isPythonSpace(r) != want[r] {
			t.Fatalf("U+%04X", r)
		}
	}
}
