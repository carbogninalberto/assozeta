package dto

import (
	"encoding/json"
	"errors"
	"os"
	"testing"
)

// TestPsycopg2LiteralMatchesGolden checks Psycopg2Literal against literals produced by real psycopg2 2.9 / libpq 16
// (testdata/psycopg2_literal.json): each entry holds json.loads' JSON text and either the mogrified literal or the
// adaptation error.
func TestPsycopg2LiteralMatchesGolden(t *testing.T) {
	raw, err := os.ReadFile("testdata/psycopg2_literal.json")
	if err != nil || len(raw) == 0 {
		t.Fatalf("corpus missing: %v", err)
	}
	var entries []struct {
		JSON    string `json:"json"`
		Literal string `json:"literal"`
		Error   string `json:"error"`
	}
	if err := json.Unmarshal(raw, &entries); err != nil || len(entries) == 0 {
		t.Fatalf("corpus unreadable: %v", err)
	}
	for index, entry := range entries {
		value, err := ParsePythonJSON(entry.JSON)
		if err != nil {
			t.Fatalf("entry %d: ParsePythonJSON(%q): %v", index, entry.JSON, err)
		}
		literal, err := Psycopg2Literal(value)
		if entry.Error != "" {
			var adaptErr *Psycopg2AdaptError
			if !errors.As(err, &adaptErr) {
				t.Errorf("entry %d: %q: err=%v want *Psycopg2AdaptError %q", index, entry.JSON, err, entry.Error)
				continue
			}
			if adaptErr.Text != entry.Error {
				t.Errorf("entry %d: %q: error=%q want %q", index, entry.JSON, adaptErr.Text, entry.Error)
			}
			continue
		}
		if err != nil {
			t.Errorf("entry %d: %q: unexpected error: %v", index, entry.JSON, err)
			continue
		}
		if literal != entry.Literal {
			t.Errorf("entry %d: %q: got %q want %q", index, entry.JSON, literal, entry.Literal)
		}
	}
}
