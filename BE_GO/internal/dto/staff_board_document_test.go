package dto

import (
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
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

// English DRF messages for the transport-neutral codes; the handler localizes.
func staffBoardInputMessage(code string) string {
	switch {
	case code == "blank":
		return "This field may not be blank."
	case code == "null":
		return "This field may not be null."
	case code == "string":
		return "Not a valid string."
	case code == "boolean":
		return "Must be a valid boolean."
	case code == "json":
		return "Value must be valid JSON."
	case code == "no-data":
		return "No data provided"
	case code == "null-character":
		return "Null characters are not allowed."
	case strings.HasPrefix(code, "max:"):
		return "Ensure this field has no more than " + strings.TrimPrefix(code, "max:") + " characters."
	case strings.HasPrefix(code, "surrogate:"):
		return "Surrogate characters are not allowed: U+" + strings.TrimPrefix(code, "surrogate:") + "."
	case strings.HasPrefix(code, "dictionary:"):
		return "Invalid data. Expected a dictionary, but got " + strings.TrimPrefix(code, "dictionary:") + "."
	case strings.HasPrefix(code, "text:"):
		return strings.TrimPrefix(code, "text:")
	}
	return "unknown code " + code
}

func TestParseStaffBoardMessageInputCorpus(t *testing.T) {
	raw, err := os.ReadFile("../../../TESTS/fixtures/staff-board-inputs.json")
	if err != nil {
		t.Fatal(err)
	}
	var corpus struct {
		Format int `json:"format"`
		Cases  []struct {
			Name     string                     `json:"name"`
			Partial  bool                       `json:"partial"`
			BodyJSON *string                    `json:"body_json"`
			Form     [][2]string                `json:"form"`
			Expect   map[string]json.RawMessage `json:"expect"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &corpus); err != nil || corpus.Format != 1 || len(corpus.Cases) < 60 {
		t.Fatal("corpus", err)
	}
	for _, c := range corpus.Cases {
		t.Run(c.Name, func(t *testing.T) {
			var body any
			form := c.BodyJSON == nil
			if form {
				fields := domain.RequestObject{}
				for _, pair := range c.Form {
					replaced := false
					for i := range fields {
						if fields[i].Name == pair[0] {
							fields[i].Value, replaced = pair[1], true
						}
					}
					if !replaced {
						fields = append(fields, domain.RequestField{Name: pair[0], Value: pair[1]})
					}
				}
				body = fields
			} else if body, err = ParseProfileTablesJSON([]byte(*c.BodyJSON)); err != nil {
				t.Fatal(err)
			}
			input, err := ParseStaffBoardMessageInput(body, form, c.Partial)
			if want, ok := c.Expect["errors"]; ok {
				var expected map[string][]string
				if err := json.Unmarshal(want, &expected); err != nil {
					t.Fatal(err)
				}
				var invalid *StaffBoardInputError
				if !errors.As(err, &invalid) {
					t.Fatalf("want errors %v, got %v", expected, err)
				}
				got := map[string][]string{}
				for _, field := range invalid.Fields {
					for _, code := range field.Codes {
						got[field.Name] = append(got[field.Name], staffBoardInputMessage(code))
					}
				}
				if gotJSON, _ := json.Marshal(got); string(gotJSON) != mustCanonical(t, want) {
					t.Fatalf("errors\n got %s\nwant %s", gotJSON, want)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			expected, err := ParseProfileTablesJSON(c.Expect["valid"])
			if err != nil {
				t.Fatal(err)
			}
			want := map[string]string{}
			for _, field := range expected.(domain.RequestObject) {
				want[field.Name] = string(PythonJSONDumps(field.Value))
			}
			got := map[string]string{}
			if input.Content != nil {
				got["content"] = string(PythonJSONDumps(*input.Content))
			}
			if input.DocumentSet {
				got["document"] = string(PythonJSONDumps(input.Document))
			}
			if input.Pinned != nil {
				got["pinned"] = string(PythonJSONDumps(*input.Pinned))
			}
			if gotJSON, _ := json.Marshal(got); string(gotJSON) != func() string { w, _ := json.Marshal(want); return string(w) }() {
				t.Fatalf("valid\n got %s\nwant %s", gotJSON, func() string { w, _ := json.Marshal(want); return string(w) }())
			}
		})
	}
}

func mustCanonical(t *testing.T, raw json.RawMessage) string {
	t.Helper()
	var value map[string][]string
	if err := json.Unmarshal(raw, &value); err != nil {
		t.Fatal(err)
	}
	out, _ := json.Marshal(value)
	return string(out)
}
