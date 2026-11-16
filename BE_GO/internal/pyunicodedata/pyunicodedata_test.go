package pyunicodedata

import (
	"encoding/json"
	"os"
	"testing"
)

// The corpus is TESTS/fixtures/generate_unicode_names.py: names with
// Python's answer, the string or not found (a null value).
func TestLookupCorpus(t *testing.T) {
	raw, err := os.ReadFile("testdata/lookup.json")
	if err != nil {
		t.Fatal(err)
	}
	var file struct {
		Version string `json:"unidata_version"`
		Cases   []struct {
			Name  string  `json:"name"`
			Value *string `json:"value"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &file); err != nil {
		t.Fatal(err)
	}
	if file.Version != "15.1.0" {
		t.Fatalf("corpus version %q", file.Version)
	}
	if len(file.Cases) < 3000 {
		t.Fatalf("%d corpus cases", len(file.Cases))
	}
	for _, item := range file.Cases {
		value, ok := Lookup(item.Name)
		switch {
		case item.Value == nil && ok:
			t.Errorf("%q: found %q", item.Name, value)
		case item.Value != nil && !ok:
			t.Errorf("%q: not found, want %q", item.Name, *item.Value)
		case item.Value != nil && value != *item.Value:
			t.Errorf("%q: got %q, want %q", item.Name, value, *item.Value)
		}
	}
}
