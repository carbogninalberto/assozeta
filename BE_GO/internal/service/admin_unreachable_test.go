package service

import (
	"context"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
)

// States no request or stored row produces are application-state errors:
// Django's 500 page, never a handoff.
func TestUnreachableStatesAreAppState(t *testing.T) {
	for name, err := range map[string]error{
		"data restore":         domain.ErrDataRestoreUnreachable,
		"data restore cleanup": domain.ErrDataRestoreCleanupUnreachable,
	} {
		if !errors.Is(err, refusal.ErrAppState) {
			t.Errorf("%s: must match ErrAppState", name)
		}
		if errors.Is(err, refusal.ErrNotPorted) {
			t.Errorf("%s: must not match ErrNotPorted", name)
		}
		if _, refused := refusal.Reason(err); refused {
			t.Errorf("%s: must not be a refusal", name)
		}
	}
}

type restoreProgressError struct{ err error }

func (p restoreProgressError) Snapshot(context.Context, string) (repository.DataRestoreProgressValue, error) {
	return repository.DataRestoreProgressValue{}, p.err
}

// progress.snapshot catches every exception and answers None: a failed cache
// read is no progress, not an error.
func TestDataRestoreSnapshotFailureIsNoProgress(t *testing.T) {
	restore := &DataRestore{progress: restoreProgressError{err: errors.New("cached value outside the subset")}}
	op := &domain.DataRestoreReceipt{ID: "op-1", State: "running", Attempts: 1}
	progress, err := restore.snapshot(context.Background(), op)
	if err != nil || progress != nil {
		t.Fatalf("got (%v, %v), want (nil, nil)", progress, err)
	}
}

// A cancelled request still reports its context error.
func TestDataRestoreSnapshotKeepsContextError(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	restore := &DataRestore{progress: restoreProgressError{err: errors.New("cache unavailable")}}
	op := &domain.DataRestoreReceipt{ID: "op-1", State: "running", Attempts: 1}
	if _, err := restore.snapshot(ctx, op); err == nil {
		t.Fatal("a cancelled request must not be swallowed as no progress")
	}
}

// A cached value outside the payload progress.py writes is unreachable state.
func TestDataRestoreRenderedRejectsUnknownValue(t *testing.T) {
	if _, err := dataRestoreRendered(struct{}{}); !errors.Is(err, domain.ErrDataRestoreUnreachable) {
		t.Fatalf("got %v, want ErrDataRestoreUnreachable", err)
	}
}
