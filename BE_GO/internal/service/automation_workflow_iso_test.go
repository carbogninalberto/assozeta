package service

import (
	"encoding/json"
	"os"
	"testing"
	"time"
)

// TestAutomationWorkflowFromISO replays testdata/fromisoformat.json
// (TESTS/adapters/go/fromisoformat_golden.py): Python's own answers.
func TestAutomationWorkflowFromISO(t *testing.T) {
	path := "testdata/fromisoformat.json"
	if extra := os.Getenv("FROMISOFORMAT_GOLDEN"); extra != "" {
		path = extra
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		Cases []struct {
			Text   string `json:"text"`
			Fields []int  `json:"fields"`
		} `json:"cases"`
	}
	if err = json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	parsed, failures := 0, 0
	for _, item := range golden.Cases {
		got, ok := automationWorkflowFromISO(item.Text)
		if ok != (item.Fields != nil) {
			failures++
			t.Errorf("%q: go ok=%v %+v, python %v", item.Text, ok, got, item.Fields)
		} else if ok {
			parsed++
			if want := (automationWorkflowISOTime{item.Fields[0], item.Fields[1], item.Fields[2], item.Fields[3], item.Fields[4], item.Fields[5],
				item.Fields[6]}); got != want {
				failures++
				t.Errorf("%q: go %+v python %+v", item.Text, got, want)
			}
		}
		if failures > 20 {
			t.FailNow()
		}
	}
	if len(golden.Cases) < 1000 || parsed < 100 {
		t.Fatalf("coverage: %d cases, %d parsed", len(golden.Cases), parsed)
	}
}

func TestAutomationWorkflowETA(t *testing.T) {
	now := time.Date(2026, 11, 10, 20, 0, 0, 123456789, time.UTC)
	for _, test := range []struct{ at, want time.Time }{
		{now.Add(90 * time.Minute), time.Date(2026, 11, 10, 21, 30, 0, 123456000, time.UTC)},
		// (at - now).total_seconds() is the float 251450107199.87656: timedelta(seconds=) lands 13 microseconds later.
		{time.Date(9994, 12, 30, 23, 59, 59, 999999000, time.UTC), time.Date(9994, 12, 31, 0, 0, 0, 12000, time.UTC)},
	} {
		if got := automationWorkflowETA(now, test.at); !got.Equal(test.want) {
			t.Errorf("eta(%v) = %v, want %v", test.at, got, test.want)
		}
	}
}
