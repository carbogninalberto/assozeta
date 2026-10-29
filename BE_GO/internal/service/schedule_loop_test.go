package service

import (
	"context"
	"errors"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"math"
	"sync/atomic"
	"testing"
	"time"
)

type loopTickFunc func(context.Context, domain.ScheduleCursor) (domain.SchedulePageResult, error)

func (f loopTickFunc) Tick(ctx context.Context, c domain.ScheduleCursor) (domain.SchedulePageResult, error) {
	return f(ctx, c)
}
func loopWait[T any](t *testing.T, ch <-chan T) T {
	t.Helper()
	select {
	case v := <-ch:
		return v
	case <-time.After(2 * time.Second):
		t.Fatal("scheduler test timed out")
		var zero T
		return zero
	}
}
func TestScheduleLoopConstructor(t *testing.T) {
	tick := loopTickFunc(func(context.Context, domain.ScheduleCursor) (domain.SchedulePageResult, error) {
		return domain.SchedulePageResult{}, nil
	})
	for _, d := range []time.Duration{0, time.Millisecond, 5 * time.Second} {
		if _, e := NewScheduleLoop(tick, ScheduleLoopOptions{PollInterval: d}); e != nil {
			t.Fatal(d, e)
		}
	}
	var nilTick loopTickFunc
	for _, tc := range []struct {
		tick  domain.ScheduleTicker
		delay time.Duration
	}{{nil, 0}, {nilTick, 0}, {tick, -1}, {tick, time.Microsecond}, {tick, 5*time.Second + 1}} {
		if _, e := NewScheduleLoop(tc.tick, ScheduleLoopOptions{PollInterval: tc.delay}); !errors.Is(e, domain.ErrScheduleLoopInvalid) {
			t.Fatalf("invalid options admitted: %v", e)
		}
	}
}
func TestScheduleLoopInvalidRunDoesNotTick(t *testing.T) {
	var calls atomic.Int32
	s, _ := NewScheduleLoop(loopTickFunc(func(context.Context, domain.ScheduleCursor) (domain.SchedulePageResult, error) {
		calls.Add(1)
		return domain.SchedulePageResult{}, nil
	}), ScheduleLoopOptions{})
	for _, c := range []domain.ScheduleCursor{{AfterID: -1}, {ThroughID: -1}, {AfterID: 2, ThroughID: 1}, {ThroughID: math.MaxInt32 + 1}} {
		if _, e := s.Run(context.Background(), c); !errors.Is(e, domain.ErrScheduleLoopInvalid) {
			t.Fatal(c, e)
		}
	}
	if _, e := s.Run(nil, domain.ScheduleCursor{}); !errors.Is(e, domain.ErrScheduleLoopInvalid) {
		t.Fatal(e)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	cancel()
	c := domain.ScheduleCursor{AfterID: 2, ThroughID: 3}
	if got, e := s.Run(ctx, c); got != c || !errors.Is(e, context.Canceled) {
		t.Fatal(got, e)
	}
	if calls.Load() != 0 {
		t.Fatal("invalid run invoked page")
	}
}
func TestScheduleLoopRetainsCursorAcrossAmbiguousErrors(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	cursors := []domain.ScheduleCursor{{}, {AfterID: 4, ThroughID: 40}, {AfterID: 16, ThroughID: 40}, {}}
	var calls int
	tick := loopTickFunc(func(_ context.Context, c domain.ScheduleCursor) (domain.SchedulePageResult, error) {
		if calls >= len(cursors) || c != cursors[calls] {
			t.Fatalf("cursor lost at %d: %+v", calls, c)
		}
		calls++
		if calls == len(cursors) {
			cancel()
			return domain.SchedulePageResult{NextCursor: domain.ScheduleCursor{AfterID: 3, ThroughID: 60}}, nil
		}
		result := domain.SchedulePageResult{NextCursor: cursors[calls]}
		if calls == 1 {
			return result, errors.New("private SQL/credentials must not be emitted")
		}
		return result, nil
	})
	s, _ := NewScheduleLoop(tick, ScheduleLoopOptions{PollInterval: time.Millisecond})
	got, e := s.Run(ctx, domain.ScheduleCursor{})
	if calls != 4 || got != (domain.ScheduleCursor{AfterID: 3, ThroughID: 60}) || !errors.Is(e, context.Canceled) {
		t.Fatal(calls, got, e)
	}
}
func TestScheduleLoopPacesEveryPageAndIgnoresRawDelay(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	const interval = 15 * time.Millisecond
	var times []time.Time
	tick := loopTickFunc(func(context.Context, domain.ScheduleCursor) (domain.SchedulePageResult, error) {
		times = append(times, time.Now())
		if len(times) == 3 {
			cancel()
		}
		return domain.SchedulePageResult{Outcomes: []domain.SchedulePageOutcome{{NextCheckAfter: time.Duration(math.MaxInt64)}}}, nil
	})
	s, _ := NewScheduleLoop(tick, ScheduleLoopOptions{PollInterval: interval})
	_, e := s.Run(ctx, domain.ScheduleCursor{})
	if !errors.Is(e, context.Canceled) || len(times) != 3 {
		t.Fatal(e, len(times))
	}
	for i := 1; i < len(times); i++ {
		if times[i].Sub(times[i-1]) < interval {
			t.Fatal("page loop spun without configured pacing")
		}
	}
}
func TestScheduleLoopDefersInactiveAndMaintenance(t *testing.T) {
	for _, fault := range []error{domain.ErrOccurrenceInactive, domain.ErrRestoreBusy, domain.ErrRestorePending, context.DeadlineExceeded, context.Canceled} {
		t.Run(fault.Error(), func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
			defer cancel()
			var calls int
			var first time.Time
			c := domain.ScheduleCursor{AfterID: 1, ThroughID: 4}
			tick := loopTickFunc(func(_ context.Context, got domain.ScheduleCursor) (domain.SchedulePageResult, error) {
				calls++
				if calls == 1 {
					first = time.Now()
					return domain.SchedulePageResult{NextCursor: c}, fault
				}
				if got != c || time.Since(first) < 10*time.Millisecond {
					t.Fatal("deferral lost cursor or spun")
				}
				cancel()
				return domain.SchedulePageResult{NextCursor: c}, nil
			})
			s, _ := NewScheduleLoop(tick, ScheduleLoopOptions{PollInterval: 10 * time.Millisecond})
			got, e := s.Run(ctx, domain.ScheduleCursor{})
			if calls != 2 || got != c || !errors.Is(e, context.Canceled) {
				t.Fatal(calls, got, e)
			}
		})
	}
}
func TestScheduleLoopCancellationJoinsActivePage(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	entered := make(chan struct{})
	cleaning := make(chan struct{})
	release := make(chan struct{})
	defer close(release)
	c := domain.ScheduleCursor{AfterID: 7, ThroughID: 9}
	var calls atomic.Int32
	tick := loopTickFunc(func(page context.Context, _ domain.ScheduleCursor) (domain.SchedulePageResult, error) {
		calls.Add(1)
		close(entered)
		<-page.Done()
		close(cleaning)
		<-release
		return domain.SchedulePageResult{NextCursor: c}, page.Err()
	})
	s, _ := NewScheduleLoop(tick, ScheduleLoopOptions{PollInterval: time.Millisecond})
	type outcome struct {
		c domain.ScheduleCursor
		e error
	}
	done := make(chan outcome, 1)
	go func() { got, e := s.Run(ctx, domain.ScheduleCursor{}); done <- outcome{got, e} }()
	loopWait(t, entered)
	cancel()
	loopWait(t, cleaning)
	select {
	case <-done:
		t.Fatal("Run escaped active page cleanup")
	case <-time.After(15 * time.Millisecond):
	}
	release <- struct{}{}
	got := loopWait(t, done)
	if got.c != c || !errors.Is(got.e, context.Canceled) || calls.Load() != 1 {
		t.Fatal(got, calls.Load())
	}
}
func TestScheduleLoopCancellationInterruptsPacing(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	entered := make(chan struct{})
	var calls atomic.Int32
	tick := loopTickFunc(func(context.Context, domain.ScheduleCursor) (domain.SchedulePageResult, error) {
		calls.Add(1)
		close(entered)
		return domain.SchedulePageResult{}, nil
	})
	s, _ := NewScheduleLoop(tick, ScheduleLoopOptions{PollInterval: 5 * time.Second})
	done := make(chan error, 1)
	go func() { _, e := s.Run(ctx, domain.ScheduleCursor{}); done <- e }()
	loopWait(t, entered)
	cancel()
	if e := loopWait(t, done); !errors.Is(e, context.Canceled) || calls.Load() != 1 {
		t.Fatal(e, calls.Load())
	}
}
func TestScheduleLoopTerminalErrorsAreRedacted(t *testing.T) {
	for _, fault := range []error{domain.ErrSchedulePageInvalid, domain.ErrSchedulePageCandidates, domain.ErrRestoreLockRequired, domain.ErrRestoreVolumeUnavailable} {
		c := domain.ScheduleCursor{AfterID: 1, ThroughID: 3}
		var calls int
		wrapped := errors.Join(fault, errors.New("private SQL postgres://password"))
		s, _ := NewScheduleLoop(loopTickFunc(func(context.Context, domain.ScheduleCursor) (domain.SchedulePageResult, error) {
			calls++
			return domain.SchedulePageResult{NextCursor: c}, wrapped
		}), ScheduleLoopOptions{PollInterval: time.Millisecond})
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		got, e := s.Run(ctx, domain.ScheduleCursor{})
		cancel()
		if got != c || calls != 1 || !errors.Is(e, fault) || e.Error() != "periodic scheduler cannot continue" {
			t.Fatal(got, calls, e)
		}
	}
}
func TestScheduleLoopRejectsMalformedReturnedCursor(t *testing.T) {
	bad := domain.ScheduleCursor{AfterID: 4, ThroughID: 2}
	var calls int
	s, _ := NewScheduleLoop(loopTickFunc(func(context.Context, domain.ScheduleCursor) (domain.SchedulePageResult, error) {
		calls++
		return domain.SchedulePageResult{NextCursor: bad}, nil
	}), ScheduleLoopOptions{})
	got, e := s.Run(context.Background(), domain.ScheduleCursor{})
	if got != bad || !errors.Is(e, domain.ErrScheduleLoopInvalid) || calls != 1 {
		t.Fatal(got, e, calls)
	}
}
func TestScheduleLoopRejectsConcurrentRunAndCanRestart(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	entered := make(chan struct{}, 1)
	s, _ := NewScheduleLoop(loopTickFunc(func(page context.Context, c domain.ScheduleCursor) (domain.SchedulePageResult, error) {
		entered <- struct{}{}
		<-page.Done()
		return domain.SchedulePageResult{NextCursor: c}, page.Err()
	}), ScheduleLoopOptions{})
	done := make(chan error, 1)
	go func() { _, e := s.Run(ctx, domain.ScheduleCursor{}); done <- e }()
	loopWait(t, entered)
	if _, e := s.Run(context.Background(), domain.ScheduleCursor{}); !errors.Is(e, domain.ErrScheduleLoopRunning) {
		t.Fatal(e)
	}
	cancel()
	loopWait(t, done)
	stopped, stop := context.WithCancel(context.Background())
	stop()
	if _, e := s.Run(stopped, domain.ScheduleCursor{}); !errors.Is(e, context.Canceled) {
		t.Fatal(e)
	}
}
