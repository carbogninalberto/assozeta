package service

import (
	"context"
	"errors"
	"testing"
	"time"
)

type probe struct {
	err     error
	calls   int
	bounded bool
}

func (p *probe) Check(ctx context.Context) error {
	p.calls++
	_, p.bounded = ctx.Deadline()
	return p.err
}
func TestReadinessChecksBothDependenciesAndDoesNotLeakErrors(t *testing.T) {
	for _, databaseOK := range []bool{false, true} {
		for _, redisOK := range []bool{false, true} {
			db, cache := &probe{}, &probe{}
			if !databaseOK {
				db.err = errors.New("private DSN")
			}
			if !redisOK {
				cache.err = errors.New("private Redis URL")
			}
			result := NewHealthService(db, cache, time.Second).Readiness(context.Background())
			if result.Checks.Database != databaseOK || result.Checks.Redis != redisOK || result.Ready() != (databaseOK && redisOK) {
				t.Fatalf("wrong readiness: %+v", result)
			}
			if db.calls != 1 || cache.calls != 1 || !db.bounded || !cache.bounded {
				t.Fatal("dependencies must be checked with a deadline")
			}
		}
	}
}
