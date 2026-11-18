package handoff

import (
	"cmp"
	"maps"
	"slices"
	"sync"
	"time"
)

// Outcome describes what happened when Serve saw a refusal.
type Outcome string

const (
	OutcomeHandedOff   Outcome = "handed_off"
	OutcomeInterrupted Outcome = "interrupted"
	OutcomeSkipped     Outcome = "skipped"
	OutcomeDisabled    Outcome = "disabled"
)

// StatsRow is the count and latest occurrence for one refusal key.
type StatsRow struct {
	Method, Route, Reason string
	Outcome               Outcome
	Cause                 string
	Count                 uint64
	Last                  time.Time
}

// StatsSnapshot contains this process's refusal counts since startup.
type StatsSnapshot struct {
	Since   time.Time
	Totals  map[Outcome]uint64
	Rows    []StatsRow
	Dropped uint64
}

const maxStatsRows = 512

type statsKey struct {
	method, route, reason string
	outcome               Outcome
	cause                 string
}

var statsSince = time.Now().UTC()

var handoffStats = struct {
	sync.Mutex
	totals  map[Outcome]uint64
	rows    map[statsKey]StatsRow
	dropped uint64
}{totals: make(map[Outcome]uint64), rows: make(map[statsKey]StatsRow)}

func recordStats(method, route, reason string, outcome Outcome, cause string) {
	key := statsKey{method: method, route: route, reason: reason, outcome: outcome, cause: cause}
	handoffStats.Lock()
	defer handoffStats.Unlock()

	handoffStats.totals[outcome]++
	row, ok := handoffStats.rows[key]
	if !ok {
		if len(handoffStats.rows) >= maxStatsRows {
			handoffStats.dropped++
			return
		}
		row = StatsRow{Method: method, Route: route, Reason: reason, Outcome: outcome, Cause: cause}
	}
	row.Count++
	row.Last = time.Now().UTC()
	handoffStats.rows[key] = row
}

// Stats returns a copy of the current process totals and sorted rows.
func Stats() StatsSnapshot {
	handoffStats.Lock()
	snapshot := StatsSnapshot{
		Since:   statsSince,
		Totals:  maps.Clone(handoffStats.totals),
		Rows:    make([]StatsRow, 0, len(handoffStats.rows)),
		Dropped: handoffStats.dropped,
	}
	for _, row := range handoffStats.rows {
		snapshot.Rows = append(snapshot.Rows, row)
	}
	handoffStats.Unlock()

	slices.SortFunc(snapshot.Rows, func(a, b StatsRow) int {
		return cmp.Or(
			cmp.Compare(b.Count, a.Count),
			cmp.Compare(a.Route, b.Route),
			cmp.Compare(a.Method, b.Method),
			cmp.Compare(a.Reason, b.Reason),
			cmp.Compare(a.Outcome, b.Outcome),
			cmp.Compare(a.Cause, b.Cause),
		)
	})
	return snapshot
}

func resetStats() {
	handoffStats.Lock()
	defer handoffStats.Unlock()
	clear(handoffStats.totals)
	clear(handoffStats.rows)
	handoffStats.dropped = 0
}
