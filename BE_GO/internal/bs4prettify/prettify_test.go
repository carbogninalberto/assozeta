package bs4prettify

import (
	"encoding/json"
	"errors"
	"os"
	"testing"
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

func TestPrettifyRefusesOutsideSubset(t *testing.T) {
	for _, markup := range []string{"<!-- c -->", "<!DOCTYPE html>", "<?x?>", "<script>x</script>", "<style>a</style>",
		"<textarea>a</textarea>", "<title>a</title>", "a < b", "a &b", "&amp", "&foo;", "&#0;", "&#128;", "&#xD800;",
		"<p class=x>", "<p a = \"b\">", "<p a=\"b\"c=\"d\">", "</br x>", "<p", "x\x00", "x\x0b", "\x85", "\xed\xa0\x80",
		"<p/ >", "<custom>", "</>", "<!x>"} {
		if _, err := Prettify(markup); !errors.Is(err, ErrUnsupported) {
			t.Errorf("%q accepted", markup)
		}
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
