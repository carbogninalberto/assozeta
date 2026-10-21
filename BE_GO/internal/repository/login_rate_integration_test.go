//go:build ratelimitintegration

package repository_test

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/redis/go-redis/v9"
)

type rateCommandHook struct {
	commands atomic.Int64
	afterSet func()
}

func (h *rateCommandHook) DialHook(next redis.DialHook) redis.DialHook {
	return func(ctx context.Context, network, address string) (net.Conn, error) {
		return next(ctx, network, address)
	}
}
func (h *rateCommandHook) ProcessPipelineHook(next redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return next
}
func (h *rateCommandHook) ProcessHook(next redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		if cmd.Name() == "set" || cmd.Name() == "eval" {
			h.commands.Add(1)
		}
		err := next(ctx, cmd)
		if cmd.Name() == "set" && h.afterSet != nil {
			h.afterSet()
		}
		return err
	}
}

func TestDjangoLoginRateInterop(t *testing.T) {
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_RATE_FIXTURE"))
	if err != nil {
		t.Fatal("run TESTS/adapters/django/login_rate.py")
	}
	var fixture struct {
		Address, Password string
		Cases             []struct {
			Name, Group, Value, Key string
			Now                     int64
			Count                   int64
			Blocked                 bool
			Existing                bool
		}
	}
	if json.Unmarshal(raw, &fixture) != nil || len(fixture.Cases) != 14 {
		t.Fatal("invalid rate fixture")
	}
	client := redis.NewClient(&redis.Options{Addr: fixture.Address, Password: fixture.Password, DB: 1, MaxRetries: -1,
		ContextTimeoutEnabled: true, DialTimeout: time.Second, ReadTimeout: time.Second, WriteTimeout: time.Second, PoolSize: 4})
	defer client.Close()
	ctx := context.Background()
	if err = client.Ping(ctx).Err(); err != nil {
		t.Fatal("private Redis unavailable")
	}
	cache, err := repository.NewDjangoLoginRateCache(client)
	if err != nil {
		t.Fatal(err)
	}
	hook := &rateCommandHook{}
	client.AddHook(hook)
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			commands := hook.commands.Load()
			before := client.PTTL(ctx, tc.Key).Val()
			gate := service.NewLoginRate(cache, func() time.Time { return time.Unix(tc.Now, 0) })
			err := gate.Check(ctx, tc.Group, tc.Value)
			if errors.Is(err, service.ErrLoginRateLimited) != tc.Blocked || (!tc.Blocked && err != nil) {
				t.Fatalf("admission differs: %v", err)
			}
			count, err := client.Get(ctx, tc.Key).Int64()
			if err != nil || count != tc.Count {
				t.Fatalf("counter differs: %d %v", count, err)
			}
			after := client.PTTL(ctx, tc.Key).Val()
			if tc.Existing {
				if after <= 0 || after > before || after > 50*time.Second {
					t.Fatal("existing counter TTL renewed")
				}
			} else if after < 60*time.Second || after > 65*time.Second {
				t.Fatal("new counter TTL differs")
			}
			want := int64(1)
			if tc.Existing {
				want = 2
			}
			if hook.commands.Load()-commands != want {
				t.Fatal("unbounded cache command count")
			}
		})
	}
	// All participants share the same Redis counter and a bounded connection pool.
	var allowed, blocked, failed atomic.Int32
	gate := service.NewLoginRate(cache, func() time.Time { return time.Unix(1800000000, 0) })
	var group sync.WaitGroup
	commands := hook.commands.Load()
	for range 64 {
		group.Add(1)
		go func() {
			defer group.Done()
			err := gate.Check(ctx, "login-account", "concurrent-fixture")
			switch {
			case err == nil:
				allowed.Add(1)
			case errors.Is(err, service.ErrLoginRateLimited):
				blocked.Add(1)
			default:
				failed.Add(1)
			}
		}()
	}
	group.Wait()
	if allowed.Load() != 5 || blocked.Load() != 59 || failed.Load() != 0 {
		t.Fatalf("concurrent admission %d/%d/%d", allowed.Load(), blocked.Load(), failed.Load())
	}
	if hook.commands.Load()-commands != 127 || client.PoolStats().TotalConns > 4 {
		t.Fatal("concurrency exceeded command or pool bounds")
	}
	cancelled, cancel := context.WithCancel(ctx)
	cancel()
	if !errors.Is(gate.Check(cancelled, "login-account", "cancelled-fixture"), context.Canceled) {
		t.Fatal("cancelled request admitted")
	}
	if err := gate.Check(ctx, "login-account", "pool-reused-fixture"); err != nil {
		t.Fatal("pool not reusable")
	}
	// Delete an actual Redis key precisely after the unsuccessful SET NX. This
	// exercises the expiration race without substituting an in-memory cache.
	admin := redis.NewClient(&redis.Options{Addr: fixture.Address, Password: fixture.Password, DB: 1, MaxRetries: -1})
	defer admin.Close()
	raceKey := "rl:" + strings.Repeat("a", 64)
	if err := admin.Set(ctx, ":1:"+raceKey, 3, time.Minute).Err(); err != nil {
		t.Fatal("race seed failed")
	}
	hook.afterSet = func() {
		if err := admin.Del(ctx, ":1:"+raceKey).Err(); err != nil {
			t.Error("race deletion failed")
		}
	}
	_, err = cache.IncrementLoginRate(ctx, raceKey)
	hook.afterSet = nil
	if !errors.Is(err, domain.ErrLoginRateCounterMissing) || admin.Exists(ctx, ":1:"+raceKey).Val() != 0 {
		t.Fatal("expiry race created a counter")
	}
	// A real paused Redis write must obey the caller deadline without retrying.
	deadlineKey := "rl:" + strings.Repeat("b", 64)
	if err := admin.Do(ctx, "CLIENT", "PAUSE", 200, "WRITE").Err(); err != nil {
		t.Fatal("cannot pause isolated Redis")
	}
	limited, stop := context.WithTimeout(ctx, 25*time.Millisecond)
	start := time.Now()
	commands = hook.commands.Load()
	_, err = cache.IncrementLoginRate(limited, deadlineKey)
	stop()
	if err == nil || time.Since(start) > time.Second || hook.commands.Load()-commands != 1 {
		t.Fatal("Redis deadline or retry bound differs")
	}
	// An in-flight command may execute after its client has timed out. Do not
	// claim cancellation rolls back Redis effects; remove this synthetic key.
	if err := admin.Del(ctx, ":1:"+deadlineKey).Err(); err != nil {
		t.Fatal("deadline cleanup failed")
	}
	if err := gate.Check(ctx, "login-account", "pool-reused-fixture"); err != nil {
		t.Fatal("pool failed after timed-out I/O")
	}
	client.Close()
	if err := gate.Check(ctx, "login-account", "closed-client-fixture"); err == nil || errors.Is(err, service.ErrLoginRateLimited) {
		t.Fatal("cache outage was admitted or disguised as a threshold")
	}
}
