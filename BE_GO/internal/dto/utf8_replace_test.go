package dto

import (
	"encoding/hex"
	"encoding/json"
	"os"
	"testing"
)

// TestPythonUTF8ReplaceMatchesCPython compares with bytes.decode('utf-8', 'replace').
func TestPythonUTF8ReplaceMatchesCPython(t *testing.T) {
	raw, err := os.ReadFile("testdata/utf8_replace.json")
	if err != nil || len(raw) == 0 {
		t.Fatalf("corpus missing: %v", err)
	}
	var rows [][2]string
	if err := json.Unmarshal(raw, &rows); err != nil || len(rows) == 0 {
		t.Fatalf("corpus unreadable: %v", err)
	}
	for _, row := range rows {
		input, _ := hex.DecodeString(row[0])
		if got := PythonUTF8Replace(string(input)); got != row[1] {
			t.Errorf("%x: got %q want %q", input, got, row[1])
		}
	}
}
