package dto

import (
	"encoding/json"
	"os"
	"testing"
)

// TestAuditParseISOMatchesCPython compares the parser with datetime.fromisoformat
// (CPython 3.13) over testdata/audit_isoformat.json: [text, [y, m, d, H, M] | null].
func TestAuditParseISOMatchesCPython(t *testing.T) {
	raw, err := os.ReadFile("testdata/audit_isoformat.json")
	if err != nil || len(raw) == 0 {
		t.Fatalf("corpus missing: %v", err)
	}
	var corpus []struct {
		Text string
		Want []int
	}
	var rows [][]json.RawMessage
	if err := json.Unmarshal(raw, &rows); err != nil || len(rows) == 0 {
		t.Fatalf("corpus unreadable: %v", err)
	}
	for _, row := range rows {
		var entry struct {
			Text string
			Want []int
		}
		_ = json.Unmarshal(row[0], &entry.Text)
		_ = json.Unmarshal(row[1], &entry.Want)
		corpus = append(corpus, entry)
	}
	valid := 0
	for _, entry := range corpus {
		got, ok := auditParseISO(replaceZ(entry.Text))
		if ok != (entry.Want != nil) {
			t.Errorf("%q: ok=%v, python valid=%v", entry.Text, ok, entry.Want != nil)
			continue
		}
		if ok {
			valid++
			if want := (auditISOMoment{entry.Want[0], entry.Want[1], entry.Want[2], entry.Want[3], entry.Want[4]}); got != want {
				t.Errorf("%q: got %+v want %+v", entry.Text, got, want)
			}
		}
	}
	if valid < 50 {
		t.Fatalf("corpus has only %d valid rows", valid)
	}
}

func replaceZ(text string) string {
	out := ""
	for _, r := range text {
		if r == 'Z' {
			out += "+00:00"
		} else {
			out += string(r)
		}
	}
	return out
}
