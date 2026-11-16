package service

import (
	"bytes"
	"encoding/json"
	"os"
	"testing"
)

// TestCeleryExceptionText replays testdata/instance_setup_celery_exceptions.json:
// the 500 body's error text of BE/instance/views.py _link_imported_instance for
// each stored FAILURE-row result. The file was produced with CPython 3.13.16
// and celery's Backend.exception_to_python (tmp/long-tail/lt4-C/excprobe/golden.py).
func TestCeleryExceptionText(t *testing.T) {
	raw, err := os.ReadFile("testdata/instance_setup_celery_exceptions.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		Producer string `json:"producer"`
		Cases    []struct {
			Label   string          `json:"label"`
			Stored  json.RawMessage `json:"stored"`
			Outcome string          `json:"outcome"`
			Text    string          `json:"text"`
			Error   string          `json:"error"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	if len(golden.Cases) == 0 || golden.Producer == "" {
		t.Fatal("golden file without cases or producer")
	}
	for _, item := range golden.Cases {
		// The result column is decoded with json.Number (celeryJSON).
		reader := json.NewDecoder(bytes.NewReader(item.Stored))
		reader.UseNumber()
		var stored any
		if err := reader.Decode(&stored); err != nil {
			t.Fatalf("%s: %v", item.Label, err)
		}
		if got := celeryExceptionText(stored); got != item.Error {
			t.Errorf("%s (%s): got %q, want %q", item.Label, item.Outcome, got, item.Error)
		}
	}
}
