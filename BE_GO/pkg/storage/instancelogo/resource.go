// Package instancelogo implements storage reads without choosing business keys.
package instancelogo

import (
	"context"
	"os"
	"sync"
)

type admission struct{ slots chan struct{} }

func newAdmission(limit int) admission { return admission{slots: make(chan struct{}, limit)} }
func (a admission) acquire(ctx context.Context) (func(), error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	select {
	case a.slots <- struct{}{}:
		return func() { <-a.slots }, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

type ownedFile struct {
	file    *os.File
	remove  string
	release func()
	done    chan struct{}
	once    sync.Once
	err     error
}

func ownFile(ctx context.Context, file *os.File, remove string, release func()) *ownedFile {
	f := &ownedFile{file: file, remove: remove, release: release, done: make(chan struct{})}
	go func() {
		select {
		case <-ctx.Done():
			f.Close()
		case <-f.done:
		}
	}()
	return f
}
func (f *ownedFile) Read(p []byte) (int, error) { return f.file.Read(p) }
func (f *ownedFile) Close() error {
	f.once.Do(func() {
		f.err = f.file.Close()
		if f.remove != "" {
			if err := os.Remove(f.remove); f.err == nil {
				f.err = err
			}
		}
		f.release()
		close(f.done)
	})
	return f.err
}
