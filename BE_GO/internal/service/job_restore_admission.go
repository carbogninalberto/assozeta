package service

import (
	"context"
	"io"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
)

type JobAdmission interface {
	Acquire(context.Context) (io.Closer, *time.Time, error)
}

// JobRestoreAdmission covers ordinary jobs. Restore execution itself requires
// its separate exclusive lifecycle protocol, not an ordinary shared-job slot.
type JobRestoreAdmission struct {
	locks      repository.RestoreOperationLocker
	states     repository.JobRestoreStateReader
	configured bool
}

func NewJobRestoreAdmission(locks repository.RestoreOperationLocker, states repository.JobRestoreStateReader, configured bool) *JobRestoreAdmission {
	return &JobRestoreAdmission{locks: locks, states: states, configured: configured}
}
func (s *JobRestoreAdmission) Acquire(ctx context.Context) (io.Closer, *time.Time, error) {
	lease, err := s.locks.Acquire(ctx, false, false)
	if err != nil {
		return nil, nil, err
	}
	if !s.configured {
		return lease, nil, nil
	}
	pending, cutoff, err := s.states.JobBarrier(ctx)
	if err != nil || pending {
		_ = lease.Close()
		if err != nil {
			return nil, nil, err
		}
		return nil, nil, domain.ErrRestorePending
	}
	return lease, cutoff, nil
}
