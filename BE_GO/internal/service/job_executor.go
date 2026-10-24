package service

import (
	"context"
	"errors"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
)

// Services own successful completion, including CompleteInTx for database-only
// effects. Returning nil without durable completion is a terminal programming
// error. No business effect may follow a service's successful completion commit.
type JobDefinition struct {
	Version int
	Timeout time.Duration
	Run     func(context.Context, domain.Job) error
}
type JobExecutorOptions struct {
	Owner                                                                               string
	Concurrency                                                                         int
	Lease, RenewInterval, StoreTimeout, PollInterval, MaintenanceInterval, DrainTimeout time.Duration
}
type JobExecutor struct {
	store       repository.JobExecutionStore
	admission   JobAdmission
	definitions map[string]JobDefinition
	kinds       []string
	options     JobExecutorOptions
}

var executorKind = regexp.MustCompile(`^[a-z][a-z0-9_.]{0,127}$`)

func NewJobExecutor(store repository.JobExecutionStore, admission JobAdmission, definitions map[string]JobDefinition, options JobExecutorOptions) (*JobExecutor, error) {
	if store == nil || admission == nil || len(definitions) == 0 || len(definitions) > 64 || len(options.Owner) == 0 || len(options.Owner) > 128 || strings.ContainsRune(options.Owner, 0) || options.Concurrency < 1 || options.Concurrency > 32 || options.Lease < time.Millisecond || options.Lease > 24*time.Hour || options.RenewInterval < time.Millisecond || options.StoreTimeout < time.Millisecond || options.RenewInterval >= options.Lease-options.StoreTimeout || options.PollInterval < time.Millisecond || options.MaintenanceInterval < time.Millisecond || options.DrainTimeout < 0 {
		return nil, domain.ErrJobInvalid
	}
	s := &JobExecutor{store: store, admission: admission, options: options, definitions: map[string]JobDefinition{}}
	for kind, definition := range definitions {
		if !executorKind.MatchString(kind) || definition.Version < 1 || definition.Timeout < time.Millisecond || definition.Timeout > 24*time.Hour || definition.Run == nil {
			return nil, domain.ErrJobInvalid
		}
		s.definitions[kind] = definition
		s.kinds = append(s.kinds, kind)
	}
	sort.Strings(s.kinds)
	return s, nil
}

func (s *JobExecutor) ExecuteNext(ctx context.Context) (bool, error) { return s.executeNext(ctx, ctx) }

func (s *JobExecutor) executeNext(intake, execution context.Context) (bool, error) {
	if err := intake.Err(); err != nil {
		return false, err
	}
	admissionCtx, cancelAdmission := context.WithTimeout(intake, s.options.StoreTimeout)
	lease, cutoff, err := s.admission.Acquire(admissionCtx)
	cancelAdmission()
	if err != nil {
		return false, err
	}
	if lease != nil {
		defer lease.Close()
	}
	claimCtx, cancelClaim := context.WithTimeout(intake, s.options.StoreTimeout)
	jobs, err := s.store.Claim(claimCtx, s.kinds, s.options.Owner, 1, s.options.Lease)
	cancelClaim()
	if err != nil {
		return false, err
	}
	if len(jobs) == 0 {
		return false, nil
	}
	if len(jobs) != 1 {
		return true, domain.ErrJobInvalid
	}
	job := jobs[0]
	fail := func(failure domain.JobFailure) (bool, error) {
		writeCtx, cancel := context.WithTimeout(context.WithoutCancel(execution), s.options.StoreTimeout)
		defer cancel()
		return true, s.store.Fail(writeCtx, job.ID, job.LeaseToken, failure.Code, failure.Retry, failure.Delay)
	}
	if cutoff != nil && job.CreatedAt.Before(*cutoff) {
		return fail(domain.JobFailure{Code: "stale_after_restore"})
	}
	definition, known := s.definitions[job.Kind]
	if !known || definition.Version != job.Version {
		return fail(domain.JobFailure{Code: "unsupported_version"})
	}
	taskCtx, cancelTask := context.WithTimeout(execution, definition.Timeout)
	defer cancelTask()
	// Cancellation asks the service to stop; renewal and the file lease stay owned
	// until it actually returns. An uncooperative goroutine cannot be safely detached.
	heartbeatCtx, stopHeartbeat := context.WithCancel(context.WithoutCancel(execution))
	defer stopHeartbeat()
	heartbeatDone := make(chan error, 1)
	go func() {
		ticker := time.NewTicker(s.options.RenewInterval)
		defer ticker.Stop()
		for {
			select {
			case <-heartbeatCtx.Done():
				heartbeatDone <- nil
				return
			case <-ticker.C:
				renewCtx, cancel := context.WithTimeout(heartbeatCtx, s.options.StoreTimeout)
				err := s.store.Renew(renewCtx, job.ID, job.LeaseToken, s.options.Lease)
				cancel()
				if err != nil {
					if heartbeatCtx.Err() != nil {
						heartbeatDone <- nil
					} else {
						cancelTask()
						heartbeatDone <- err
					}
					return
				}
			}
		}
	}()
	handlerErr := invokeJob(taskCtx, definition.Run, job)
	taskErr := taskCtx.Err()
	stopHeartbeat()
	renewErr := <-heartbeatDone
	readCtx, cancelRead := context.WithTimeout(context.WithoutCancel(execution), s.options.StoreTimeout)
	current, readErr := s.store.Get(readCtx, job.AssociationID, job.ID)
	cancelRead()
	// A completion committed just before renewal can legitimately make Renew
	// report lease loss. Only this exact attempt's durable success resolves it.
	if readErr == nil && current.State == "completed" && current.Attempts == job.Attempts {
		return true, nil
	}
	if renewErr != nil {
		return true, renewErr
	}
	if readErr != nil {
		return true, readErr
	}
	if current.State != "running" || current.LeaseToken != job.LeaseToken || current.Attempts != job.Attempts {
		return true, domain.ErrJobLeaseLost
	}
	if execution.Err() != nil {
		return fail(domain.JobFailure{Code: "worker_shutdown", Retry: true})
	}
	var declared *domain.JobFailure
	if errors.As(handlerErr, &declared) && declared != nil {
		return fail(*declared)
	}
	if errors.Is(taskErr, context.DeadlineExceeded) {
		return fail(domain.JobFailure{Code: "task_timeout"})
	}
	if handlerErr != nil {
		return fail(domain.JobFailure{Code: "task_failed"})
	}
	return fail(domain.JobFailure{Code: "completion_missing"})
}

func invokeJob(ctx context.Context, run func(context.Context, domain.Job) error, job domain.Job) (err error) {
	defer func() {
		if recover() != nil {
			err = &domain.JobFailure{Code: "task_panicked"}
		}
	}()
	return run(ctx, job)
}

// Run stops intake first, allows admitted services to finish during the grace
// period, then cancels their contexts and joins every slot. It never releases a
// restore lease while its service is still running. Production service calls
// must be context-aware and bounded; process supervision is the final hard stop.
func (s *JobExecutor) Run(ctx context.Context) error {
	intake, stopIntake := context.WithCancel(ctx)
	defer stopIntake()
	execution, cancelExecution := context.WithCancel(context.WithoutCancel(ctx))
	defer cancelExecution()
	failures := make(chan error, s.options.Concurrency)
	var slots sync.WaitGroup
	for i := 0; i < s.options.Concurrency; i++ {
		slots.Add(1)
		go func() {
			defer slots.Done()
			for intake.Err() == nil {
				ran, err := s.executeNext(intake, execution)
				delay := s.options.PollInterval
				if errors.Is(err, domain.ErrRestoreBusy) || errors.Is(err, domain.ErrRestorePending) || errors.Is(err, domain.ErrRestoreVolumeUnavailable) {
					delay = s.options.MaintenanceInterval
				} else if err != nil {
					if ran || intake.Err() == nil {
						failures <- err
						stopIntake()
					}
					return
				}
				if ran {
					continue
				}
				timer := time.NewTimer(delay)
				select {
				case <-intake.Done():
					timer.Stop()
					return
				case <-timer.C:
				}
			}
		}()
	}
	done := make(chan struct{})
	go func() { slots.Wait(); close(done) }()
	drained := false
	select {
	case <-done:
		drained = true
	case <-intake.Done():
	}
	expired := false
	if !drained {
		timer := time.NewTimer(s.options.DrainTimeout)
		select {
		case <-done:
			timer.Stop()
		case <-timer.C:
			expired = true
			cancelExecution()
			<-done
		}
	}
	close(failures)
	var result error
	for err := range failures {
		result = errors.Join(result, err)
	}
	if expired {
		result = errors.Join(result, domain.ErrJobDrainTimeout)
	}
	return result
}
