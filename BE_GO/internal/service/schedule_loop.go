package service

import (
	"context"
	"errors"
	"math"
	"sync/atomic"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type ScheduleLoopOptions struct {
	// PollInterval is a positive delay after every completed page, including a
	// deferred or failed page. Zero selects one second; valid explicit values
	// are one millisecond through five seconds. It is not a per-source SLA.
	PollInterval time.Duration
}

type ScheduleLoop struct {
	ticker   domain.ScheduleTicker
	interval time.Duration
	running  atomic.Bool
}

func NewScheduleLoop(ticker domain.ScheduleTicker, options ScheduleLoopOptions) (*ScheduleLoop, error) {
	if options.PollInterval == 0 {
		options.PollInterval = time.Second
	}
	if nilSchedulePageDependency(ticker) || options.PollInterval < time.Millisecond || options.PollInterval > 5*time.Second {
		return nil, domain.ErrScheduleLoopInvalid
	}
	return &ScheduleLoop{ticker: ticker, interval: options.PollInterval}, nil
}

// Run calls pages serially; it never starts a page in a background goroutine.
// Cancellation therefore joins the active page and its cleanup before returning.
// Cleanup may outlast work deadlines. Each returned cursor is retained before
// examining an error: an ambiguous publication is not retried immediately.
//
// Temporary SQL failures, inactive ownership and maintenance defer with the same
// bounded positive pacing. The command's ticker wrapper must expose those errors
// as degraded/deferred status; continuing Run is not a successful publication or
// authority heartbeat. Structural/configuration errors terminate with safe text.
//
// Raw NextCheckAfter values cannot suppress future metadata reloads: this loop
// does not use them as sleep durations. A finite high-water rotation still takes
// time proportional to its page count, actual page work and configured pacing.
func (s *ScheduleLoop) Run(ctx context.Context, cursor domain.ScheduleCursor) (domain.ScheduleCursor, error) {
	if ctx == nil || !validLoopCursor(cursor) {
		return cursor, domain.ErrScheduleLoopInvalid
	}
	if !s.running.CompareAndSwap(false, true) {
		return cursor, domain.ErrScheduleLoopRunning
	}
	defer s.running.Store(false)
	for {
		if err := ctx.Err(); err != nil {
			return cursor, err
		}
		page, err := s.ticker.Tick(ctx, cursor)
		cursor = page.NextCursor
		if !validLoopCursor(cursor) {
			return cursor, domain.ErrScheduleLoopInvalid
		}
		if cancelled := ctx.Err(); cancelled != nil {
			return cursor, cancelled
		}
		if errors.Is(err, domain.ErrSchedulePageInvalid) || errors.Is(err, domain.ErrSchedulePageCandidates) || errors.Is(err, domain.ErrRestoreLockRequired) || errors.Is(err, domain.ErrRestoreVolumeUnavailable) {
			return cursor, scheduleLoopFailure{cause: err}
		}
		timer := time.NewTimer(s.interval)
		select {
		case <-ctx.Done():
			timer.Stop()
			return cursor, ctx.Err()
		case <-timer.C:
		}
	}
}
func validLoopCursor(c domain.ScheduleCursor) bool {
	return c.AfterID >= 0 && c.ThroughID >= 0 && c.AfterID <= c.ThroughID && c.ThroughID <= math.MaxInt32
}

type scheduleLoopFailure struct{ cause error }

func (scheduleLoopFailure) Error() string   { return "periodic scheduler cannot continue" }
func (e scheduleLoopFailure) Unwrap() error { return e.cause }
