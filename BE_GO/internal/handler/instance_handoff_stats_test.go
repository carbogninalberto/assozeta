package handler

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/handoff"
)

// The beta report is Go-only (Django has no such view): its body is fixed here.
func TestInstanceHandoffStatsBody(t *testing.T) {
	since := time.Date(2026, 11, 18, 9, 0, 0, 0, time.UTC)
	last := time.Date(2026, 11, 18, 10, 30, 15, 0, time.UTC)
	snapshot := handoff.StatsSnapshot{
		Since: since,
		Totals: map[handoff.Outcome]uint64{
			handoff.OutcomeHandedOff: 4, handoff.OutcomeSkipped: 1,
		},
		Rows: []handoff.StatsRow{
			{Method: "GET", Route: "/payment/list", Reason: "r1", Outcome: handoff.OutcomeHandedOff, Count: 4, Last: last},
			{Method: "POST", Route: "/payment/add", Reason: "r2", Outcome: handoff.OutcomeSkipped, Cause: "effect", Count: 1, Last: last},
		},
		Dropped: 2,
	}
	raw, err := json.Marshal(instanceHandoffStatsBody(snapshot, true))
	if err != nil {
		t.Fatal(err)
	}
	want := `{"beta":true,"handoffEnabled":true,"since":"2026-11-18T09:00:00Z",` +
		`"totals":{"handedOff":4,"interrupted":0,"skipped":1,"disabled":0},"dropped":2,` +
		`"routes":[` +
		`{"method":"GET","route":"/payment/list","reason":"r1","outcome":"handed_off","cause":"","count":4,"lastAt":"2026-11-18T10:30:15Z"},` +
		`{"method":"POST","route":"/payment/add","reason":"r2","outcome":"skipped","cause":"effect","count":1,"lastAt":"2026-11-18T10:30:15Z"}]}`
	if string(raw) != want {
		t.Fatalf("body\n got %s\nwant %s", raw, want)
	}

	empty, err := json.Marshal(instanceHandoffStatsBody(handoff.StatsSnapshot{Since: since}, false))
	if err != nil {
		t.Fatal(err)
	}
	wantEmpty := `{"beta":true,"handoffEnabled":false,"since":"2026-11-18T09:00:00Z",` +
		`"totals":{"handedOff":0,"interrupted":0,"skipped":0,"disabled":0},"dropped":0,"routes":[]}`
	if string(empty) != wantEmpty {
		t.Fatalf("empty body\n got %s\nwant %s", empty, wantEmpty)
	}
}
