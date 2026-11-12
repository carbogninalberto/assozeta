// Package paymentcategorylifecycle owns the lifetime of payment-category
// mutations from their admission probes through driver cleanup. Client
// disconnects must not abort admitted mutations, but shutdown expiry must.
package paymentcategorylifecycle

import (
	"context"
	"errors"
	"sync"
)

var ErrAdmissionStopped = errors.New("payment category admission stopped")

// Operation tracks one Add, Update, or Delete handler from admission probes
// through mutation completion. Its request context remains effective until
// Admit is called; the admitted context retains request values but not client
// cancellation.
type Operation struct {
	lifecycle      *Lifecycle
	requestContext context.Context
	preAdmission   context.Context
	cancel         context.CancelFunc
	released       bool
}

// Lifecycle is a server-scoped admission registry. It is deliberately separate
// from request cancellation and contains no package-global state.
type Lifecycle struct {
	mu            sync.Mutex
	accepting     bool
	operations    map[*Operation]struct{}
	drained       chan struct{}
	drainedClosed bool
}

func New() *Lifecycle {
	return &Lifecycle{
		accepting:  true,
		operations: make(map[*Operation]struct{}),
		drained:    make(chan struct{}),
	}
}

// Track registers a mutation handler before its first admission probe. The
// operation context retains normal request cancellation until Admit succeeds.
func (l *Lifecycle) Track(requestContext context.Context) (*Operation, error) {
	if err := requestContext.Err(); err != nil {
		return nil, err
	}
	operationContext, cancel := context.WithCancel(requestContext)

	l.mu.Lock()
	if !l.accepting {
		l.mu.Unlock()
		cancel()
		return nil, ErrAdmissionStopped
	}
	// Close the cancellation race before registering.
	if err := requestContext.Err(); err != nil {
		l.mu.Unlock()
		cancel()
		return nil, err
	}
	tracked := &Operation{
		lifecycle:      l,
		requestContext: requestContext,
		preAdmission:   operationContext,
		cancel:         cancel,
	}
	l.operations[tracked] = struct{}{}
	l.mu.Unlock()
	return tracked, nil
}

// Context is the pre-admission context. It honors client cancellation and a
// process-drain expiry while retaining request values.
func (o *Operation) Context() context.Context {
	return o.preAdmission
}

// Admit atomically rejects mutations after draining starts and replaces the
// request-cancelable context with one canceled only by client-independent
// lifecycle expiry.
func (o *Operation) Admit() (context.Context, error) {
	lifecycle := o.lifecycle
	lifecycle.mu.Lock()
	if o.released {
		lifecycle.mu.Unlock()
		return nil, ErrAdmissionStopped
	}
	if !lifecycle.accepting {
		lifecycle.mu.Unlock()
		return nil, ErrAdmissionStopped
	}
	if err := o.requestContext.Err(); err != nil {
		lifecycle.mu.Unlock()
		return nil, err
	}
	operationContext, cancel := context.WithCancel(context.WithoutCancel(o.requestContext))
	previousCancel := o.cancel
	o.cancel = cancel
	lifecycle.mu.Unlock()
	previousCancel()
	return operationContext, nil
}

// Release removes the tracked handler and cancels any remaining context. The
// handler calls it only after the service has joined owned-transaction cleanup.
func (o *Operation) Release() {
	lifecycle := o.lifecycle
	lifecycle.mu.Lock()
	if o.released {
		lifecycle.mu.Unlock()
		return
	}
	o.released = true
	cancel := o.cancel
	delete(lifecycle.operations, o)
	lifecycle.closeDrainedLocked()
	lifecycle.mu.Unlock()
	cancel()
}

// StopAdmission prevents new mutations from starting. Admitted contexts remain
// live so a graceful HTTP drain can preserve client-disconnected work.
func (l *Lifecycle) StopAdmission() {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.accepting = false
	l.closeDrainedLocked()
}

// Expire revokes every tracked operation, including admitted and pre-admission
// cleanup. It is called only after the graceful HTTP drain deadline has expired.
func (l *Lifecycle) Expire() {
	l.mu.Lock()
	l.accepting = false
	cancels := make([]context.CancelFunc, 0, len(l.operations))
	for tracked := range l.operations {
		cancels = append(cancels, tracked.cancel)
	}
	l.closeDrainedLocked()
	l.mu.Unlock()

	for _, cancel := range cancels {
		cancel()
	}
}

// Wait returns after admission has stopped and all registered operations have
// released. The caller bounds the wait; operation release itself still waits for
// the underlying service and owned transaction cleanup.
func (l *Lifecycle) Wait(ctx context.Context) error {
	l.mu.Lock()
	drained := l.drained
	l.mu.Unlock()
	select {
	case <-drained:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (l *Lifecycle) closeDrainedLocked() {
	if l.accepting || len(l.operations) != 0 || l.drainedClosed {
		return
	}
	l.drainedClosed = true
	close(l.drained)
}
