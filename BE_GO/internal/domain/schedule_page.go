package domain

import (
	"context"
	"errors"
	"time"
)

var ErrSchedulePageInvalid = errors.New("invalid periodic page bounds or cursor")
var ErrSchedulePageCandidates = errors.New("invalid periodic candidate page")

// PeriodicCandidate contains only bounded discovery metadata. Publication must
// reread its source and definition under the existing ownership/restore locks.
type PeriodicCandidate struct {
	ID      int64
	Task    string
	Enabled bool
}

type PeriodicCandidateStore interface {
	HighWater(context.Context) (int64, error)
	ListCandidates(context.Context, int64, int64, int) ([]PeriodicCandidate, error)
}

type OccurrencePublisher interface {
	PublishNext(context.Context, int64) (OccurrenceResult, error)
}

type SchedulePageStatus string

const (
	SchedulePageDisabled       SchedulePageStatus = "disabled"
	SchedulePageUnrelated      SchedulePageStatus = "unrelated"
	SchedulePageWaiting        SchedulePageStatus = "waiting"
	SchedulePagePublished      SchedulePageStatus = "published"
	SchedulePageUnsupported    SchedulePageStatus = "unsupported"
	SchedulePageBusy           SchedulePageStatus = "busy"
	SchedulePageNotFound       SchedulePageStatus = "not_found"
	SchedulePageInactive       SchedulePageStatus = "inactive"
	SchedulePageRestoreBlocked SchedulePageStatus = "restore_blocked"
	SchedulePageCancelled      SchedulePageStatus = "cancelled"
	SchedulePageFailed         SchedulePageStatus = "failed"
)

type SchedulePageOutcome struct {
	SourceID int64
	Status   SchedulePageStatus
	// Raw evaluator delay, never an unrestricted scheduler sleep instruction.
	NextCheckAfter time.Duration
}

// ScheduleCursor freezes the upper ID for one finite rotation.
type ScheduleCursor struct {
	AfterID   int64
	ThroughID int64
}

// The caller owns NextCursor, including on error. EndOfRange is true only after
// consuming the frozen range; it resets NextCursor to the zero pair.
// New higher IDs are deferred to the next finite rotation. An attempted failure
// advances AfterID but retains ThroughID; its source is retried on a later
// rotation, never automatically within the same page.
// A bounded page does not promise every source is revisited within five seconds.
type SchedulePageResult struct {
	NextCursor ScheduleCursor
	EndOfRange bool
	Outcomes   []SchedulePageOutcome
}
