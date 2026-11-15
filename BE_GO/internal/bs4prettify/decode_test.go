package bs4prettify

import (
	"encoding/json"
	"errors"
	"os"
	"testing"
)

// decodeCase is one testdata/decode_python.json entry: a golden value
// recorded by testdata/gen_decode_python.py, or the exception bs4 raised.
type decodeCase struct {
	Input  pyText  `json:"input"`
	Output *pyText `json:"output"`
	Error  *string `json:"error"`
}

// TestDecodeGolden compares Parse + Decode with
// str(BeautifulSoup(markup, "html.parser")) on the inputs of the prettify
// corpus and on one document per NavigableString subclass. Decode is
// Tag.decode() without indentation, so it also proves the entity
// substitution and the prefix/suffix of comments, CDATA sections,
// declarations, doctypes and processing instructions.
func TestDecodeGolden(t *testing.T) {
	raw, err := os.ReadFile("testdata/decode_python.json")
	if err != nil {
		t.Fatal(err)
	}
	var document struct {
		BS4   string       `json:"bs4"`
		Cases []decodeCase `json:"cases"`
	}
	if err := json.Unmarshal(raw, &document); err != nil {
		t.Fatal(err)
	}
	if document.BS4 != "4.13.3" || len(document.Cases) < 4000 {
		t.Fatalf("golden data: bs4 %s, %d cases", document.BS4, len(document.Cases))
	}
	reported, mismatches := 0, 0
	for i, c := range document.Cases {
		problem := ""
		root, err := Parse(c.Input.value)
		switch {
		case c.Error != nil && errors.Is(err, ErrPythonRaises):
			// bs4 raises on this markup, this port reports it.
		case c.Error != nil && err != nil:
			problem = "got " + err.Error() + ", want " + *c.Error
		case c.Error != nil:
			problem = "accepted markup bs4 raises " + *c.Error + " on"
		case err != nil:
			problem = "Parse returned " + err.Error() + ", want " + c.Output.value
		case c.Output == nil:
			problem = "golden data has neither output nor error"
		default:
			if got := root.Decode(); got != c.Output.value {
				problem = "Decode differs\n got " + quoteText(got) + "\nwant " + quoteText(c.Output.value)
			}
		}
		if problem == "" {
			continue
		}
		mismatches++
		if reported < 25 {
			reported++
			t.Errorf("case %d input %s: %s", i, quoteText(c.Input.value), problem)
		}
	}
	if mismatches > 0 {
		t.Fatalf("%d of %d cases mismatch", mismatches, len(document.Cases))
	}
	t.Logf("%d str(soup) cases match", len(document.Cases))
}

// quoteText keeps a golden failure readable when the value holds control
// characters or lone surrogates.
func quoteText(text string) string {
	encoded, err := json.Marshal(text)
	if err != nil {
		return text
	}
	return string(encoded)
}
