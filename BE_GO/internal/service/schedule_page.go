package service

import (
	"context"
	"errors"
	"math"
	"reflect"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type SchedulePageOptions struct {
	Limit         int
	TickTimeout   time.Duration
	SourceTimeout time.Duration
}

type SchedulePager struct {
	store     domain.PeriodicCandidateStore
	publisher domain.OccurrencePublisher
	options   SchedulePageOptions
}

func NewSchedulePager(store domain.PeriodicCandidateStore, publisher domain.OccurrencePublisher, options SchedulePageOptions) (*SchedulePager, error) {
	if options.Limit == 0 {
		options.Limit = 16
	}
	if options.TickTimeout == 0 {
		options.TickTimeout = 2 * time.Second
	}
	if options.SourceTimeout == 0 {
		options.SourceTimeout = 500 * time.Millisecond
	}
	if nilSchedulePageDependency(store) || nilSchedulePageDependency(publisher) || options.Limit < 1 || options.Limit > 64 || options.TickTimeout < time.Millisecond || options.TickTimeout > 5*time.Second || options.SourceTimeout < time.Millisecond || options.SourceTimeout > options.TickTimeout {
		return nil, domain.ErrSchedulePageInvalid
	}
	return &SchedulePager{store: store, publisher: publisher, options: options}, nil
}

// An interface containing a typed nil is not itself nil. Reject it before Tick
// can invoke a method on a missing dependency.
func nilSchedulePageDependency(value any) bool {
	if value == nil {
		return true
	}
	v := reflect.ValueOf(value)
	switch v.Kind() {
	case reflect.Chan, reflect.Func, reflect.Interface, reflect.Map, reflect.Pointer, reflect.Slice:
		return v.IsNil()
	default:
		return false
	}
}

// Tick processes one page, with no loop, sleep, goroutine, retry or cached source
// definition. The caller retains the cursor even on failure. A frozen high-water
// ID makes each rotation finite under continuous inserts; new IDs wait for the
// next rotation. Per-source revisit time still depends on cardinality and load.
//
// Deadlines require context-aware collaborators. Skipped metadata is read-only;
// this is not a global owner/restore heartbeat. Publication independently obtains
// the existing admission/ownership locks and rereads the current definition.
func (s *SchedulePager) Tick(ctx context.Context, cursor domain.ScheduleCursor) (domain.SchedulePageResult, error) {
	result := domain.SchedulePageResult{NextCursor: cursor}
	if ctx == nil || cursor.AfterID < 0 || cursor.ThroughID < 0 || cursor.ThroughID > math.MaxInt32 || cursor.AfterID > cursor.ThroughID {
		return result, domain.ErrSchedulePageInvalid
	}
	ctx, cancel := context.WithTimeout(ctx, s.options.TickTimeout)
	defer cancel()
	if err := ctx.Err(); err != nil {
		return result, err
	}
	if cursor.ThroughID == 0 {
		through, err := s.store.HighWater(ctx)
		if err != nil {
			return result, safeSchedulePageError(err)
		}
		if through < 0 || through > math.MaxInt32 {
			return result, domain.ErrSchedulePageCandidates
		}
		result.NextCursor.ThroughID = through
		if through == 0 {
			result.EndOfRange = true
			return result, nil
		}
	}
	if err := ctx.Err(); err != nil {
		return result, err
	}
	rows, err := s.store.ListCandidates(ctx, result.NextCursor.AfterID, result.NextCursor.ThroughID, s.options.Limit)
	if err != nil {
		return result, safeSchedulePageError(err)
	}
	// Validate the complete bounded response before any possible publication.
	if len(rows) > s.options.Limit {
		return result, domain.ErrSchedulePageCandidates
	}
	previous := result.NextCursor.AfterID
	for _, row := range rows {
		if row.ID <= previous || row.ID > result.NextCursor.ThroughID {
			return result, domain.ErrSchedulePageCandidates
		}
		previous = row.ID
	}
	if err := ctx.Err(); err != nil {
		return result, err
	}
	result.Outcomes = make([]domain.SchedulePageOutcome, 0, len(rows))
	for _, row := range rows {
		if err := ctx.Err(); err != nil {
			return result, err
		}
		// Advance only when this row is considered. Error does not mean an
		// ambiguous commit is safe to retry immediately; a later rotation rereads it.
		result.NextCursor.AfterID = row.ID
		outcome := domain.SchedulePageOutcome{SourceID: row.ID}
		if !row.Enabled {
			outcome.Status = domain.SchedulePageDisabled
		} else if row.Task != domain.JobResetDailyEmailBalance {
			outcome.Status = domain.SchedulePageUnrelated
		} else {
			sourceCtx, cancelSource := context.WithTimeout(ctx, s.options.SourceTimeout)
			publication, publishErr := s.publisher.PublishNext(sourceCtx, row.ID)
			cancelSource()
			if publishErr == nil {
				outcome.Status = domain.SchedulePageWaiting
				if publication.Published {
					outcome.Status = domain.SchedulePagePublished
				}
				outcome.NextCheckAfter = publication.NextCheckAfter
			} else {
				var terminal error
				outcome.Status, terminal = classifySchedulePageError(publishErr)
				result.Outcomes = append(result.Outcomes, outcome)
				if terminal != nil {
					return result, terminal
				}
				continue
			}
		}
		result.Outcomes = append(result.Outcomes, outcome)
	}
	if len(rows) < s.options.Limit || result.NextCursor.AfterID == result.NextCursor.ThroughID {
		result.NextCursor = domain.ScheduleCursor{}
		result.EndOfRange = true
	}
	return result, nil
}

func classifySchedulePageError(err error) (domain.SchedulePageStatus, error) {
	switch {
	case errors.Is(err, domain.ErrScheduleUnsupported), errors.Is(err, domain.ErrScheduleInvalid):
		return domain.SchedulePageUnsupported, nil
	case errors.Is(err, domain.ErrOccurrenceBusy):
		return domain.SchedulePageBusy, nil
	case errors.Is(err, domain.ErrOccurrenceNotFound):
		return domain.SchedulePageNotFound, nil
	case errors.Is(err, domain.ErrOccurrenceInactive):
		return domain.SchedulePageInactive, domain.ErrOccurrenceInactive
	case errors.Is(err, domain.ErrRestoreBusy):
		return domain.SchedulePageRestoreBlocked, domain.ErrRestoreBusy
	case errors.Is(err, domain.ErrRestorePending):
		return domain.SchedulePageRestoreBlocked, domain.ErrRestorePending
	case errors.Is(err, domain.ErrRestoreVolumeUnavailable):
		return domain.SchedulePageRestoreBlocked, domain.ErrRestoreVolumeUnavailable
	case errors.Is(err, domain.ErrRestoreLockRequired):
		return domain.SchedulePageRestoreBlocked, domain.ErrRestoreLockRequired
	case errors.Is(err, context.Canceled), errors.Is(err, context.DeadlineExceeded):
		return domain.SchedulePageCancelled, safeSchedulePageError(err)
	default:
		return domain.SchedulePageFailed, safeSchedulePageError(err)
	}
}

// Keep error matching for internal recovery without rendering SQL/credentials in
// the message returned to a future command or diagnostic endpoint.
type schedulePageFailure struct{ cause error }

func (e schedulePageFailure) Error() string { return "periodic page operation failed" }
func (e schedulePageFailure) Unwrap() error { return e.cause }
func safeSchedulePageError(err error) error {
	if errors.Is(err, context.Canceled) {
		return context.Canceled
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return context.DeadlineExceeded
	}
	return schedulePageFailure{cause: err}
}
