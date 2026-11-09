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
		{`[{"value":"cron","data":{"time":"2099-01-01T10:00","triggered":true}}]`, true, automationWorkflowTriggerNotPorted},
		{`[{"value":"cron","data":{"time":"2026-11-09T12:30"}}]`, false, automationWorkflowTriggerNotPorted},
		{`[{"value":"cron","data":{"time":"20200101"}}]`, false, automationWorkflowTriggerNotPorted},
		{`[{"value":"cron","data":{"time":"2020-01-01T10"}}]`, false, automationWorkflowTriggerNotPorted},
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
		reset, outcome := automationWorkflowEnablePlan(tree, now, rome)
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

func TestAutomationWorkflowPatchFormTreeRefused(t *testing.T) {
	_, _, err := dto.ParseAutomationWorkflowPatch(domain.RequestObject{{Name: "automation_tree", Value: "[]"}}, true)
	if err != dto.ErrAutomationWorkflowFormTreeNotPorted {
		t.Fatalf("got %v", err)
	}
}
