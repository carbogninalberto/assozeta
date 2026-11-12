package service

import (
	"context"
	"io"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
)

type RestoreAdmission struct {
	locks      repository.RestoreOperationLocker
	states     repository.RestoreStateReader
	configured bool
}

func NewRestoreAdmission(locks repository.RestoreOperationLocker, states repository.RestoreStateReader, configured bool) *RestoreAdmission {
	return &RestoreAdmission{locks: locks, states: states, configured: configured}
}

// AcquireHTTP preserves the existing operational exemptions. The caller owns
// the returned shared lease through all admitted synchronous work, even when
// that work returns an error. No database connection is retained by the lease.
func (s *RestoreAdmission) AcquireHTTP(ctx context.Context, method, path string) (io.Closer, error) {
	if method == "OPTIONS" || path == "/oauth2/refresh-token" || strings.HasPrefix(path, "/instance/admin/data-restore") {
		return nil, nil
	}
	if method == "GET" || method == "HEAD" {
		switch path {
		case "/instance/access", "/instance/config", "/instance/status", "/profile/info", "/readyz", "/healthz":
			return nil, nil
		}
	}
	lease, err := s.locks.Acquire(ctx, false, false)
	if err != nil {
		return nil, err
	}
	if s.configured && method != "GET" && method != "HEAD" {
		pending, err := s.states.Pending(ctx)
		if err != nil {
			_ = lease.Close()
			return nil, err
		}
		if pending {
			_ = lease.Close()
			return nil, domain.ErrRestorePending
		}
	}
	return lease, nil
}
