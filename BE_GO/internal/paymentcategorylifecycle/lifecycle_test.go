package paymentcategorylifecycle

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"
)

type lifecycleContextKey struct{}

func TestLifecycleAdmittedOperationSurvivesClientCancellationAndExpires(t *testing.T) {
	lifecycle := New()
	requestContext, cancelRequest := context.WithCancel(context.WithValue(context.Background(), lifecycleContextKey{}, "request-value"))
	operation, err := lifecycle.Track(requestContext)
	if err != nil {
		t.Fatal(err)
	}
	defer operation.Release()
	if got := operation.Context().Value(lifecycleContextKey{}); got != "request-value" {
		t.Fatalf("pre-admission context value = %v", got)
	}

	admittedContext, err := operation.Admit()
	if err != nil {
		t.Fatal(err)
	}
	if got := admittedContext.Value(lifecycleContextKey{}); got != "request-value" {
		t.Fatalf("admitted context value = %v", got)
	}
	cancelRequest()
	<-requestContext.Done()
	if err := admittedContext.Err(); err != nil {
		t.Fatalf("client cancellation reached admitted operation: %v", err)
	}

	lifecycle.StopAdmission()
	if err := admittedContext.Err(); err != nil {
		t.Fatalf("graceful drain canceled admitted operation: %v", err)
	}
	lifecycle.Expire()
	select {
	case <-admittedContext.Done():
	case <-time.After(time.Second):
		t.Fatal("drain expiry did not cancel admitted operation")
	}
	if !errors.Is(admittedContext.Err(), context.Canceled) {
		t.Fatalf("admitted context error = %v", admittedContext.Err())
	}
}

func TestLifecyclePreadmissionOperationPreservesClientCancellation(t *testing.T) {
	lifecycle := New()
	requestContext, cancelRequest := context.WithCancel(context.Background())
	operation, err := lifecycle.Track(requestContext)
	if err != nil {
		t.Fatal(err)
	}
	defer operation.Release()

	cancelRequest()
	<-requestContext.Done()
	if !errors.Is(operation.Context().Err(), context.Canceled) {
		t.Fatalf("pre-admission context did not retain client cancellation: %v", operation.Context().Err())
	}
	admittedContext, err := operation.Admit()
	if !errors.Is(err, context.Canceled) || admittedContext != nil {
		t.Fatalf("cancelled pre-admission dispatch = %v, %v", admittedContext, err)
	}
}

func TestLifecycleExpiryCancelsPreadmissionCleanup(t *testing.T) {
	lifecycle := New()
	operation, err := lifecycle.Track(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	defer operation.Release()

	lifecycle.StopAdmission()
	if err := operation.Context().Err(); err != nil {
		t.Fatalf("graceful drain canceled pre-admission work: %v", err)
	}
	lifecycle.Expire()
	if !errors.Is(operation.Context().Err(), context.Canceled) {
		t.Fatalf("pre-admission context error = %v", operation.Context().Err())
	}
}

func TestLifecycleRejectsNewTrackingAndMutationAdmissionAfterStop(t *testing.T) {
	lifecycle := New()
	operation, err := lifecycle.Track(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	lifecycle.StopAdmission()
	if admittedContext, err := operation.Admit(); !errors.Is(err, ErrAdmissionStopped) || admittedContext != nil {
		t.Fatalf("mutation admission after stop = %v, %v", admittedContext, err)
	}
	operation.Release()
	if tracked, err := lifecycle.Track(context.Background()); !errors.Is(err, ErrAdmissionStopped) || tracked != nil {
		t.Fatalf("tracking after stop = %v, %v", tracked, err)
	}
	lifecycle.Expire()
	if tracked, err := lifecycle.Track(context.Background()); !errors.Is(err, ErrAdmissionStopped) || tracked != nil {
		t.Fatalf("tracking after expiry = %v, %v", tracked, err)
	}
}

func TestLifecycleWaitWaitsForReleaseAndThenDoesNotLeak(t *testing.T) {
	lifecycle := New()
	operation, err := lifecycle.Track(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	lifecycle.StopAdmission()

	waitContext, cancelWait := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancelWait()
	if err := lifecycle.Wait(waitContext); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("wait with outstanding operation = %v", err)
	}

	operation.Release()
	operation.Release()
	waitContext, cancelWait = context.WithTimeout(context.Background(), time.Second)
	defer cancelWait()
	if err := lifecycle.Wait(waitContext); err != nil {
		t.Fatalf("wait after release: %v", err)
	}
}

func TestLifecycleConcurrentTrackingAdmissionAndExpiry(t *testing.T) {
	lifecycle := New()
	baseline, err := lifecycle.Track(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	baselineContext, err := baseline.Admit()
	if err != nil {
		t.Fatal(err)
	}

	const workers = 64
	start := make(chan struct{})
	results := make(chan struct {
		operation *Operation
		admitted  context.Context
		err       error
	}, workers)
	var ready, complete sync.WaitGroup
	ready.Add(workers)
	complete.Add(workers)
	for range workers {
		go func() {
			defer complete.Done()
			ready.Done()
			<-start
			operation, trackErr := lifecycle.Track(context.Background())
			if trackErr != nil {
				results <- struct {
					operation *Operation
					admitted  context.Context
					err       error
				}{err: trackErr}
				return
			}
			admittedContext, admitErr := operation.Admit()
			results <- struct {
				operation *Operation
				admitted  context.Context
				err       error
			}{operation: operation, admitted: admittedContext, err: admitErr}
		}()
	}
	ready.Wait()
	close(start)
	lifecycle.Expire()
	complete.Wait()
	close(results)

	if !errors.Is(baselineContext.Err(), context.Canceled) {
		t.Fatalf("baseline operation survived expiry: %v", baselineContext.Err())
	}
	baseline.Release()
	for result := range results {
		if result.err != nil {
			if !errors.Is(result.err, ErrAdmissionStopped) {
				t.Fatalf("concurrent admission error = %v", result.err)
			}
		} else if !errors.Is(result.admitted.Err(), context.Canceled) {
			t.Fatalf("concurrently admitted operation survived expiry: %v", result.admitted.Err())
		}
		if result.operation != nil {
			result.operation.Release()
		}
	}

	waitContext, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := lifecycle.Wait(waitContext); err != nil {
		t.Fatalf("concurrent drain leaked operation: %v", err)
	}
}
