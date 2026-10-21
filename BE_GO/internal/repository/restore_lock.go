package repository

import (
	"context"
	"errors"
	"io"
	"os"
	"sync"
	"syscall"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// RestoreFileLock uses the existing Django/CLI flock inode; it never creates a
// second coordination namespace or holds a database connection while waiting.
// Self-host Linux and the macOS development environment both support flock.
type RestoreFileLock struct{ path string }

func NewRestoreFileLock(path string) *RestoreFileLock { return &RestoreFileLock{path: path} }

type emptyRestoreLease struct{}

func (emptyRestoreLease) Close() error { return nil }

type restoreFileLease struct {
	file *os.File
	once sync.Once
	err  error
}

func (l *restoreFileLease) Close() error { l.once.Do(func() { l.err = l.file.Close() }); return l.err }

func (l *RestoreFileLock) Acquire(ctx context.Context, exclusive, required bool) (io.Closer, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if l.path == "" {
		if required {
			return nil, domain.ErrRestoreLockRequired
		}
		return emptyRestoreLease{}, nil
	}
	file, err := os.OpenFile(l.path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0666)
	if err != nil {
		return nil, domain.ErrRestoreVolumeUnavailable
	}
	mode := syscall.LOCK_SH | syscall.LOCK_NB
	if exclusive {
		mode = syscall.LOCK_EX | syscall.LOCK_NB
	}
	if err = syscall.Flock(int(file.Fd()), mode); err != nil {
		_ = file.Close()
		if errors.Is(err, syscall.EWOULDBLOCK) || errors.Is(err, syscall.EAGAIN) {
			return nil, domain.ErrRestoreBusy
		}
		return nil, err
	}
	if err = ctx.Err(); err != nil {
		_ = file.Close()
		return nil, err
	}
	return &restoreFileLease{file: file}, nil
}
