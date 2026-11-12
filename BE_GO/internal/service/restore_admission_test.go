package service

import (
	"context"
	"errors"
	"io"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type restoreLeaseStub struct{ closed int }

func (l *restoreLeaseStub) Close() error { l.closed++; return nil }

type restoreLockStub struct {
	calls   int
	failure error
	lease   restoreLeaseStub
}

func (l *restoreLockStub) Acquire(ctx context.Context, exclusive, required bool) (io.Closer, error) {
	l.calls++
	if exclusive || required {
		return nil, errors.New("HTTP admission must use optional shared lock")
	}
	if l.failure != nil {
		return nil, l.failure
	}
	return &l.lease, nil
}

type restoreStateStub struct {
	calls   int
	pending bool
	failure error
}

func (s *restoreStateStub) Pending(context.Context) (bool, error) {
	s.calls++
	return s.pending, s.failure
}

func TestRestoreAdmissionRetainsLeaseThroughCallerWork(t *testing.T) {
	for _, method := range []string{"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"} {
		for _, configured := range []bool{false, true} {
			lock, state := &restoreLockStub{}, &restoreStateStub{}
			gate := NewRestoreAdmission(lock, state, configured)
			lease, err := gate.AcquireHTTP(context.Background(), method, "/two-fa/info")
			if err != nil || lease == nil || lock.calls != 1 || lock.lease.closed != 0 {
				t.Fatal("lease was not retained through caller work")
			}
			want := 0
			if configured && method != "GET" && method != "HEAD" {
				want = 1
			}
			if state.calls != want {
				t.Fatal("wrong pending-state query admission")
			}
			if err := lease.Close(); err != nil || lock.lease.closed != 1 {
				t.Fatal("caller could not release lease")
			}
		}
	}
}

func TestRestoreAdmissionBypassesOnlyExistingOperationalPaths(t *testing.T) {
	for _, method := range []string{"GET", "HEAD", "POST", "OPTIONS"} {
		for _, path := range []string{"/instance/access", "/instance/config", "/instance/status", "/profile/info", "/readyz", "/healthz", "/oauth2/refresh-token", "/instance/admin/data-restore", "/instance/admin/data-restore-extra", "/two-fa/info"} {
			lock, state := &restoreLockStub{}, &restoreStateStub{}
			lease, err := NewRestoreAdmission(lock, state, true).AcquireHTTP(context.Background(), method, path)
			if err != nil {
				t.Fatal(err)
			}
			exempt := method == "OPTIONS" || path == "/oauth2/refresh-token" || path == "/instance/admin/data-restore" || path == "/instance/admin/data-restore-extra" || ((method == "GET" || method == "HEAD") && path != "/two-fa/info")
			if (lock.calls == 0) != exempt {
				t.Fatalf("wrong bypass for %s %s", method, path)
			}
			if lease != nil {
				_ = lease.Close()
			}
		}
	}
}

func TestRestoreAdmissionClosesLeaseOnPendingOrQueryFailure(t *testing.T) {
	failure := errors.New("private fixture database failure")
	for _, test := range []struct {
		pending       bool
		failure, want error
	}{{true, nil, domain.ErrRestorePending}, {false, failure, failure}} {
		lock, state := &restoreLockStub{}, &restoreStateStub{pending: test.pending, failure: test.failure}
		lease, err := NewRestoreAdmission(lock, state, true).AcquireHTTP(context.Background(), "POST", "/two-fa/info")
		if lease != nil || !errors.Is(err, test.want) || lock.lease.closed != 1 || state.calls != 1 {
			t.Fatal("failed admission leaked lease or lost error")
		}
	}
	lock, state := &restoreLockStub{failure: domain.ErrRestoreBusy}, &restoreStateStub{}
	lease, err := NewRestoreAdmission(lock, state, true).AcquireHTTP(context.Background(), "POST", "/two-fa/info")
	if lease != nil || !errors.Is(err, domain.ErrRestoreBusy) || state.calls != 0 {
		t.Fatal("lock denial queried state or admitted work")
	}
}
