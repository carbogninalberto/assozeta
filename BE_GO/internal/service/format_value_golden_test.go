package service

import (
	"encoding/json"
	"os"
	"testing"
)

// TestFormatValueGolden compares mcpFormatExportValue with exporters.format_value
// (CPython 3.13) on the texts of TESTS/fixtures/generate_format_value_golden.py.
func TestFormatValueGolden(t *testing.T) {
	raw, err := os.ReadFile("testdata/format_value_golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var rows [][2]string
	if err = json.Unmarshal(raw, &rows); err != nil {
		t.Fatal(err)
	}
	if len(rows) < 1000 {
		t.Fatalf("golden corpus too small: %d", len(rows))
	}
	rewritten := 0
	for _, row := range rows {
		got, err := mcpFormatExportValue(row[0])
		if err != nil {
			t.Errorf("%q: %v", row[0], err)
			continue
		}
		if got != any(row[1]) {
			t.Errorf("%q: got %q, want %q", row[0], got, row[1])
		}
		if row[0] != row[1] {
			rewritten++
		}
	}
	if rewritten < 200 {
		t.Fatalf("only %d rewritten texts in the corpus", rewritten)
	}
}
