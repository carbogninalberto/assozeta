package domain

import (
	"context"
	"errors"
)

var (
	ErrScheduleLoopInvalid = errors.New("invalid periodic scheduler configuration or cursor")
	ErrScheduleLoopRunning = errors.New("periodic scheduler is already running")
)

// ScheduleTicker completes one bounded page and joins any transaction cleanup
// before returning. Results carry the authoritative continuation cursor even on
// an ambiguous error. Implementations must observe context cancellation.
//
// A command can wrap this interface to emit sanitized diagnostics for errors or
// deferrals. Successful metadata-only pages do not prove publication ownership,
// restore admission, or database effects.
type ScheduleTicker interface {
	Tick(context.Context, ScheduleCursor) (SchedulePageResult, error)
}
