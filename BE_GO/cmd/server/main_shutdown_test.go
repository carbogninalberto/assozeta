package main

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

type shutdownServerTest struct {
	events   *[]string
	shutdown error
	t        *testing.T
}

func (s shutdownServerTest) Shutdown(ctx context.Context) error {
	*s.events = append(*s.events, "shutdown")
	deadline, ok := ctx.Deadline()
	if !ok {
		s.t.Fatal("HTTP drain context has no deadline")
	}
	remaining := time.Until(deadline)
	if remaining <= httpDrainTimeout-time.Second || remaining > httpDrainTimeout {
		s.t.Fatalf("production HTTP drain deadline = %v, want %v", remaining, httpDrainTimeout)
	}
	return s.shutdown
}

func (s shutdownServerTest) Close() error {
	*s.events = append(*s.events, "close")
	return nil
}

type shutdownOperationsTest struct {
	events    *[]string
	cleanup   error
	cleanupAt time.Duration
	t         *testing.T
}

func (s *shutdownOperationsTest) StopAdmission() {
	*s.events = append(*s.events, "stop")
}

func (s *shutdownOperationsTest) Expire() {
	*s.events = append(*s.events, "expire")
}

func (s *shutdownOperationsTest) Wait(ctx context.Context) error {
	*s.events = append(*s.events, "wait")
	deadline, ok := ctx.Deadline()
	if !ok {
		s.t.Fatal("operation cleanup context has no deadline")
	}
	remaining := time.Until(deadline)
	if remaining <= 0 || remaining > operationCleanupTimeout {
		s.t.Fatalf("operation cleanup deadline = %v, want positive and at most %v", remaining, operationCleanupTimeout)
	}
	s.cleanupAt = remaining
	return s.cleanup
}

func TestProductionDrainDeadlineRemainsTwentyFiveSeconds(t *testing.T) {
	if httpDrainTimeout != 25*time.Second {
		t.Fatalf("production HTTP drain timeout = %v", httpDrainTimeout)
	}
	if operationCleanupTimeout <= 0 || operationCleanupTimeout > 5*time.Second {
		t.Fatalf("operation cleanup timeout = %v", operationCleanupTimeout)
	}
}

func TestDrainHTTPServerGracefulCompletionDoesNotExpireAdmittedWork(t *testing.T) {
	events := []string{}
	server := shutdownServerTest{events: &events, t: t}
	operations := &shutdownOperationsTest{events: &events, t: t}

	if err := drainHTTPServer(server, operations); err != nil {
		t.Fatal(err)
	}
	if got, want := strings.Join(events, ","), "stop,shutdown"; got != want {
		t.Fatalf("graceful shutdown order = %q, want %q", got, want)
	}
}

func TestDrainHTTPServerExpiryClosesSocketsBeforeCancelingAndWaiting(t *testing.T) {
	events := []string{}
	server := shutdownServerTest{events: &events, shutdown: context.DeadlineExceeded, t: t}
	operations := &shutdownOperationsTest{events: &events, t: t}

	err := drainHTTPServer(server, operations)
	if err == nil || err.Error() != httpDrainDeadlineMessage {
		t.Fatalf("expired drain error = %v", err)
	}
	if got, want := strings.Join(events, ","), "stop,shutdown,close,expire,wait"; got != want {
		t.Fatalf("expired shutdown order = %q, want %q", got, want)
	}
	if operations.cleanupAt <= 0 {
		t.Fatal("cleanup wait was not bounded")
	}
}

func TestDrainHTTPServerReportsCleanupDeadline(t *testing.T) {
	events := []string{}
	cleanupErr := errors.New("cleanup still active")
	server := shutdownServerTest{events: &events, shutdown: context.DeadlineExceeded, t: t}
	operations := &shutdownOperationsTest{events: &events, cleanup: cleanupErr, t: t}

	err := drainHTTPServer(server, operations)
	if !errors.Is(err, cleanupErr) || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("cleanup deadline lost: %v", err)
	}
}
