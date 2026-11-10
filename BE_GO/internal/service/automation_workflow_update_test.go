package service

import (
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

func TestAutomationWorkflowEnablePlan(t *testing.T) {
	rome, err := time.LoadLocation("Europe/Rome")
	if err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 11, 9, 12, 0, 0, 0, time.UTC)
	for _, test := range []struct {
		tree    string
		reset   bool
		outcome automationWorkflowTriggerOutcome
	}{
		{`[{"value":"new_subscription"}]`, false, automationWorkflowTriggerNone},
		{`[{"value":"cron","data":{"time":"2020-01-01T10:00","triggered":true}}]`, true, automationWorkflowTriggerNone},
		{`[{"value":"cron","data":{"time":"2020-01-01","triggered":1}}]`, false, automationWorkflowTriggerNone},
		{`[{"value":"cron","data":{"time":"2099-01-01T10:00","triggered":true}}]`, true, automationWorkflowTriggerSchedule},
		{`[{"value":"cron","data":{"time":"2026-11-09T12:30"}}]`, false, automationWorkflowTriggerNone},
		{`[{"value":"cron","data":{"time":"2026-11-09T13:30"}}]`, false, automationWorkflowTriggerSchedule},
		{`[{"value":"cron","data":{"time":"20200101"}}]`, false, automationWorkflowTriggerNone},
		{`[{"value":"cron","data":{"time":"2020-01-01T10"}}]`, false, automationWorkflowTriggerNone},
		{`[{"value":"cron","data":{"time":"2099-W01-1 1030"}}]`, false, automationWorkflowTriggerSchedule},
		{`[{"value":"cron","data":{"time":"0001-01-01T10:00"}}]`, false, automationWorkflowTriggerError},
		{`[{"value":"cron","data":{"time":"0001-01-02T10:00"}}]`, false, automationWorkflowTriggerNone},
		{`[{"value":"cron","data":{"time":"9999-12-31T10:00"}}]`, false, automationWorkflowTriggerError},
		{`[{"value":"cron","data":{"time":"9999-12-30T10:00"}}]`, false, automationWorkflowTriggerSchedule},
		{`[{"value":"cron","data":{"time":"2020-01-01T10:00:00Z"}}]`, false, automationWorkflowTriggerError},
		{`[{"value":"cron","data":{"time":"2020-02-30T10:00"}}]`, false, automationWorkflowTriggerError},
		{`[{"value":"cron","data":{"time":"domani"}}]`, false, automationWorkflowTriggerError},
		{`[{"value":"cron","data":"triggered"}]`, false, automationWorkflowTriggerError},
		{`[{"value":null}]`, false, automationWorkflowTriggerNone},
		{`[{"value":null},{"id":"message"}]`, false, automationWorkflowTriggerError},
		{`[]`, false, automationWorkflowTriggerError},
		{`["testo"]`, false, automationWorkflowTriggerError},
		{`[{"id":"trigger"}]`, false, automationWorkflowTriggerError},
	} {
		tree, err := dto.ParseProfileTablesJSON([]byte(test.tree))
		if err != nil {
			t.Fatal(err)
		}
		reset, outcome, _ := automationWorkflowEnablePlan(tree, now, rome)
		if reset != test.reset || outcome != test.outcome {
			t.Fatalf("%s: got %v %v, want %v %v", test.tree, reset, outcome, test.reset, test.outcome)
		}
	}
	tree, _ := dto.ParseProfileTablesJSON([]byte(`[{"value":"cron","data":{"triggered":true,"time":"2020-01-01"}},{"id":"m"}]`))
	reset := automationWorkflowResetTriggered(tree)
	if got := string(dto.PythonJSONDumpsSorted(reset)); got != `[{"data": {"time": "2020-01-01", "triggered": false}, "value": "cron"}, {"id": "m"}]` {
		t.Fatalf("reset tree %s", got)
	}
	if got := string(dto.PythonJSONDumpsSorted(tree)); got != `[{"data": {"time": "2020-01-01", "triggered": true}, "value": "cron"}, {"id": "m"}]` {
		t.Fatalf("original tree changed %s", got)
	}
}

// pytz's localize(is_dst=False) of Europe/Rome wall times: standard offset for
// ambiguous and skipped times, +01:00 after the table's last transition.
func TestAutomationWorkflowRomeLocalize(t *testing.T) {
	rome, err := time.LoadLocation("Europe/Rome")
	if err != nil {
		t.Fatal(err)
	}
	for _, test := range []struct {
		wall [6]int
		want string
	}{
		{[6]int{2027, 1, 15, 10, 30, 0}, "2027-01-15T09:30:00Z"},
		{[6]int{2027, 7, 1, 8, 0, 0}, "2027-07-01T06:00:00Z"},
		{[6]int{2027, 10, 31, 2, 30, 0}, "2027-10-31T01:30:00Z"},
		{[6]int{2027, 3, 28, 2, 30, 0}, "2027-03-28T01:30:00Z"},
		{[6]int{2037, 10, 25, 2, 30, 0}, "2037-10-25T01:30:00Z"},
		{[6]int{2038, 7, 1, 10, 0, 0}, "2038-07-01T09:00:00Z"},
		{[6]int{2099, 7, 1, 8, 0, 0}, "2099-07-01T07:00:00Z"},
	} {
		w := test.wall
		got := automationWorkflowRomeLocalize(w[0], w[1], w[2], w[3], w[4], w[5], 0, rome).UTC().Format(time.RFC3339)
		if got != test.want {
			t.Fatalf("%v: got %s, want %s", w, got, test.want)
		}
	}
}

func TestAutomationWorkflowPatchFormTreeRefused(t *testing.T) {
	_, _, err := dto.ParseAutomationWorkflowPatch(domain.RequestObject{{Name: "automation_tree", Value: "[]"}}, true)
	if err != dto.ErrAutomationWorkflowFormTreeNotPorted {
		t.Fatalf("got %v", err)
	}
}
