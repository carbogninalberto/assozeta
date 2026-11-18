package dto

import (
	"encoding/json"
	"os"
	"strconv"
	"testing"
)

func goldenString(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}

// TestPsycopg2ServerErrorTextMatchesGolden checks Psycopg2ServerErrorText against psycopg2's str(e) for server errors
// raised by real statements (testdata/libpq_error_position.json).
func TestPsycopg2ServerErrorTextMatchesGolden(t *testing.T) {
	raw, err := os.ReadFile("testdata/libpq_error_position.json")
	if err != nil || len(raw) == 0 {
		t.Fatalf("corpus missing: %v", err)
	}
	var entries []struct {
		SQL           string  `json:"sql"`
		Message       string  `json:"message"`
		Position      *string `json:"position"`
		Detail        *string `json:"detail"`
		Hint          *string `json:"hint"`
		Context       *string `json:"context"`
		InternalQuery *string `json:"internal_query"`
		Text          string  `json:"text"`
		OK            bool    `json:"ok"`
	}
	if err := json.Unmarshal(raw, &entries); err != nil || len(entries) == 0 {
		t.Fatalf("corpus unreadable: %v", err)
	}
	for index, entry := range entries {
		if entry.OK {
			continue
		}
		position := 0
		if entry.Position != nil {
			position, err = strconv.Atoi(*entry.Position)
			if err != nil {
				t.Fatalf("entry %d: position %q: %v", index, *entry.Position, err)
			}
		}
		got := Psycopg2ServerErrorText(entry.SQL, entry.Message, int32(position),
			goldenString(entry.Detail), goldenString(entry.Hint),
			goldenString(entry.InternalQuery), goldenString(entry.Context))
		if got != entry.Text {
			t.Errorf("entry %d: sql %q:\n got %q\nwant %q", index, entry.SQL, got, entry.Text)
		}
	}
}

func TestLibpqErrorPosition(t *testing.T) {
	query := "SELECT 1"
	cases := []struct {
		name     string
		position int
		want     string
	}{
		{"zero", 0, ""},
		{"negative", -1, ""},
		{"beyond query", len(query) + 2, ""},
		{"first column", 1, "LINE 1: SELECT 1\n        ^\n"},
		{"last column", len(query), "LINE 1: SELECT 1\n               ^\n"},
	}
	for _, testCase := range cases {
		if got := LibpqErrorPosition(query, testCase.position); got != testCase.want {
			t.Errorf("%s: position %d: got %q want %q", testCase.name, testCase.position, got, testCase.want)
		}
	}
	if got := LibpqErrorPosition("", 0); got != "" {
		t.Errorf("empty query: got %q want %q", got, "")
	}
}
