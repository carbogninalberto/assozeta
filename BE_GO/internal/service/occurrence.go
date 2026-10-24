package service

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
)

type OccurrencePublisher struct {
	store  domain.OccurrenceStore
	locks  repository.RestoreOperationLocker
	states repository.RestoreStateReader
}

func NewOccurrencePublisher(store domain.OccurrenceStore, locks repository.RestoreOperationLocker, states repository.RestoreStateReader) *OccurrencePublisher {
	return &OccurrencePublisher{store: store, locks: locks, states: states}
}

// PublishNext operates on one explicitly selected persisted row. It does not
// seed schedules, scan candidates, select ownership or start a scheduler loop.
// NextCheckAfter is the raw ModelEntry delay, not a safe unbounded polling delay.
func (s *OccurrencePublisher) PublishNext(ctx context.Context, sourceID int64) (domain.OccurrenceResult, error) {
	var result domain.OccurrenceResult
	lease, err := s.locks.Acquire(ctx, false, true)
	if err != nil {
		return result, err
	}
	defer lease.Close()
	// New publication has no pre-restore work to invalidate; only an active
	// restore blocks intake. Worker execution separately checks its cutoff.
	pending, err := s.states.Pending(ctx)
	if err != nil {
		return result, err
	}
	if pending {
		return result, domain.ErrRestorePending
	}
	tx, err := s.store.BeginOccurrence(ctx, sourceID)
	if err != nil {
		return result, err
	}
	defer tx.Rollback()
	snapshot := tx.Snapshot()
	due, err := EvaluateScheduleDue(snapshot.Schedule, snapshot.Now)
	if err != nil {
		return result, err
	}
	result.NextCheckAfter = due.NextCheckAfter
	if !due.Due {
		return result, nil
	}
	receipt, err := tx.Publish(ctx)
	if err != nil {
		return domain.OccurrenceResult{}, err
	}
	if err = tx.Commit(); err != nil {
		return domain.OccurrenceResult{}, err
	}
	result.Published = true
	result.Receipt = receipt
	return result, nil
}
