package domain

import (
	"encoding/json"
	"errors"
	"time"
)

var (
	ErrScheduleUnsupported = errors.New("periodic schedule is outside the supported evaluator subset")
	ErrScheduleInvalid     = errors.New("invalid periodic schedule clock or cursor")
)

// PeriodicSchedule is a persisted definition snapshot, not an instruction to
// schedule work. A future repository must read it under its ownership/source
// locks. No static app schedule is seeded or inferred by this type.
//
// The first evaluator supports only the instance daily email reset, seconds
// intervals or scalar UTC daily cron, empty arguments/headers and default
// transport. Explicit unsupported options remain represented so callers cannot
// accidentally discard them while mapping a Django row.
type PeriodicSchedule struct {
	Task          string
	Enabled       bool
	LastRunAt     *time.Time
	DateChanged   time.Time
	StartTime     *time.Time
	Interval      *PeriodicInterval
	Cron          *PeriodicCron
	HasSolar      bool
	HasClocked    bool
	Args          json.RawMessage
	Kwargs        json.RawMessage
	Headers       json.RawMessage
	Queue         string
	Exchange      string
	RoutingKey    string
	Priority      *int
	ExpiresAt     *time.Time
	ExpireSeconds *int
	OneOff        bool
}

type PeriodicInterval struct {
	Every  int64
	Period string
}

type PeriodicCron struct {
	Minute      string
	Hour        string
	DayOfWeek   string
	DayOfMonth  string
	MonthOfYear string
	Timezone    string
}

// ScheduleDue is a pure timing decision, not ownership or publication authority.
// A future atomic publisher must record the supplied dispatch time as the new
// cursor, rather than replaying every missed slot or advancing to a nominal slot.
// NextCheckAfter is the raw ModelEntry delay; a future loop must separately retain
// bounded rechecks and reloads, as DatabaseScheduler does, rather than sleep
// unconditionally for this duration.
type ScheduleDue struct {
	Due            bool
	NextCheckAfter time.Duration
}
