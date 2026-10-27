package dto

import (
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"
)

type staffBoardDocumentCase struct {
	Name         string `json:"name"`
	DocumentJSON string `json:"document_json"`
	Repeat       *struct {
		Unit  string `json:"unit"`
		Count int    `json:"count"`
	} `json:"repeat"`
	Expect map[string]json.RawMessage `json:"expect"`
}

// The authored corpus is shared with the Django reference runner.
func TestValidateStaffBoardDocumentCorpus(t *testing.T) {
	raw, err := os.ReadFile("../../../TESTS/fixtures/staff-board-documents.json")
	if err != nil {
		t.Fatal(err)
	}
	var corpus struct {
		Format           int                      `json:"format"`
		MaxDocumentBytes int                      `json:"max_document_bytes"`
		Cases            []staffBoardDocumentCase `json:"cases"`
	}
	if err := json.Unmarshal(raw, &corpus); err != nil || corpus.Format != 1 || corpus.MaxDocumentBytes != StaffBoardDocumentMaxBytes || len(corpus.Cases) < 100 {
		t.Fatal("corpus", err, corpus.Format, corpus.MaxDocumentBytes, len(corpus.Cases))
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			input := c.DocumentJSON
			if c.Repeat != nil {
				input = strings.Replace(input, "@@REPEAT@@", strings.Repeat(c.Repeat.Unit, c.Repeat.Count), 1)
			}
			document, err := ParseProfileTablesJSON([]byte(input))
			if err != nil {
				t.Fatal("parse", err)
			}
			cleaned, text, err := ValidateStaffBoardDocument(document)
			if want, ok := c.Expect["error"]; ok {
				var message string
				_ = json.Unmarshal(want, &message)
				var invalid *StaffBoardDocumentError
				if !errors.As(err, &invalid) || invalid.Message != message {
					t.Fatalf("want %q, got %v", message, err)
				}
				return
			}
			if _, ok := c.Expect["exception"]; ok {
				if !errors.Is(err, ErrStaffBoardDocumentUnhashable) {
					t.Fatalf("want unhashable lookup failure, got %v", err)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			wantCleaned, wantText := c.Expect["cleaned_json"], c.Expect["text_json"]
			if expected, ok := c.Expect["cleaned"]; ok {
				parsed, err := ParseProfileTablesJSON(expected)
				if err != nil {
					t.Fatal(err)
				}
				wantCleaned, _ = json.Marshal(string(PythonJSONDumps(parsed)))
				wantText, _ = json.Marshal(string(PythonJSONDumps(textValue(t, c.Expect["text"]))))
			}
			var cleanedJSON, textJSON string
			if json.Unmarshal(wantCleaned, &cleanedJSON) != nil || json.Unmarshal(wantText, &textJSON) != nil {
				t.Fatal("expectation encoding")
			}
			if got := string(PythonJSONDumps(cleaned)); got != cleanedJSON {
				t.Fatalf("cleaned\n got %.300s\nwant %.300s", got, cleanedJSON)
			}
			if got := string(PythonJSONDumps(text)); got != textJSON {
				t.Fatalf("text\n got %.200s\nwant %.200s", got, textJSON)
			}
		})
	}
}

func textValue(t *testing.T, raw json.RawMessage) string {
	t.Helper()
	value, err := ParseProfileTablesJSON(raw)
	text, ok := value.(string)
	if err != nil || !ok {
		t.Fatal("text expectation", err)
	}
	return text
}

func TestPythonJSONDumpsNumbersAndEscapes(t *testing.T) {
	for input, want := range map[string]string{
		`1e300`: `1e+300`, `1e400`: `Infinity`, `-1e400`: `-Infinity`, `-0`: `0`, `0e5`: `0.0`,
		`-0.0`: `-0.0`, `1e16`: `1e+16`, `123456789012345678.0`: `1.2345678901234568e+17`,
		`0.0001`: `0.0001`, `0.00001`: `1e-05`, `1.5`: `1.5`, `100`: `100`, `1E2`: `100.0`,
		`123456789012345678901234567890`: `123456789012345678901234567890`,
		`"é😀\u0000\u007f\"\\\n\t/"`:      `"\u00e9\ud83d\ude00\u0000\u007f\"\\\n\t/"`,
		`"\ud800"`:                       `"\ud800"`, `{"a":[1,true,null],"b":{}}`: `{"a": [1, true, null], "b": {}}`, `[]`: `[]`,
	} {
		value, err := ParseProfileTablesJSON([]byte(input))
		if err != nil {
			t.Fatal(input, err)
		}
		if got := string(PythonJSONDumps(value)); got != want {
			t.Errorf("%s: got %s want %s", input, got, want)
		}
	}
}
