//go:build bs4integration

package bs4prettify

import (
	"encoding/json"
	"errors"
	"os"
	"testing"
)

// integrationCase is one entry of the corpus written by
// TESTS/adapters/django/bs4_prettify_corpus.py. Both the editor/subset corpus
// and the full corpus arrive here.
type integrationCase struct {
	Kind   string  `json:"kind"`
	Subset bool    `json:"subset"`
	Input  pyText  `json:"input"`
	Output *pyText `json:"output"` // nil: bs4 raised
}

// TestPrettifyAgainstBS4 requires bs4's exact output for every corpus case;
// a null output means bs4 raises and Prettify must report ErrPythonRaises.
func TestPrettifyAgainstBS4(t *testing.T) {
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_BS4_CORPUS"))
	if err != nil {
		t.Fatal(err)
	}
	var cases []integrationCase
	if err := json.Unmarshal(raw, &cases); err != nil || len(cases) < 10000 {
		t.Fatal("corpus", len(cases), err)
	}
	subset, accepted, reported, mismatches := 0, 0, 0, 0
	for i, c := range cases {
		if c.Subset {
			subset++
		}
		got, err := Prettify(c.Input.value)
		problem := ""
		switch {
		case c.Output == nil && errors.Is(err, ErrPythonRaises):
			accepted++
		case c.Output == nil && err == nil:
			problem = "accepted markup bs4 rejects"
		case c.Output == nil:
			problem = "error is not ErrPythonRaises: " + err.Error()
		case errors.Is(err, ErrUnsupported):
			problem = "returned ErrUnsupported"
		case err != nil:
			problem = "error: " + err.Error()
		case got != c.Output.value:
			problem = "output differs"
		default:
			accepted++
		}
		if problem == "" {
			continue
		}
		mismatches++
		if reported < 25 {
			reported++
			t.Errorf("case %d (%s) input %q: %s\n got %q\nwant %q", i, c.Kind, c.Input.value, problem, got, outputText(c.Output))
		}
	}
	if mismatches > 0 {
		t.Fatalf("%d of %d cases mismatch", mismatches, len(cases))
	}
	t.Logf("%d cases, %d subset, %d accepted", len(cases), subset, accepted)
}

func outputText(output *pyText) string {
	if output == nil {
		return "<bs4 raises>"
	}
	return output.value
}
