package repository

import (
	"context"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// Intercept before go-redis opens a connection. These tests check the Go
// contract only; health_integration_test.go checks the Lua on actual Redis.
type readinessHook struct {
	process func(context.Context, redis.Cmder) error
	calls   int
}

func (h *readinessHook) DialHook(redis.DialHook) redis.DialHook {
	return func(context.Context, string, string) (net.Conn, error) {
		return nil, errors.New("readiness unit test attempted a socket")
	}
}

func (h *readinessHook) ProcessPipelineHook(redis.ProcessPipelineHook) redis.ProcessPipelineHook {
	return func(context.Context, []redis.Cmder) error {
		return errors.New("unexpected readiness pipeline")
	}
}

func (h *readinessHook) ProcessHook(redis.ProcessHook) redis.ProcessHook {
	return func(ctx context.Context, cmd redis.Cmder) error {
		h.calls++
		return h.process(ctx, cmd)
	}
}

func readinessTestClient(t *testing.T, process func(context.Context, redis.Cmder) error) (*redis.Client, *readinessHook) {
	t.Helper()
	client := redis.NewClient(&redis.Options{MaxRetries: -1, ContextTimeoutEnabled: true})
	t.Cleanup(func() { _ = client.Close() })
	hook := &readinessHook{process: process}
	client.AddHook(hook)
	return client, hook
}

func readinessEvalArgs(t *testing.T, cmd redis.Cmder) (string, string) {
	t.Helper()
	args := cmd.Args()
	if cmd.Name() != "eval" || len(args) != 5 || args[2] != 1 || args[4] != "ok" {
		t.Fatalf("want exactly EVAL script 1 unique-key ok, got %v", args)
	}
	key, ok := args[3].(string)
	if !ok || !strings.HasPrefix(key, "assozeta:go:readiness:") {
		t.Fatalf("invalid readiness key: %v", args[3])
	}
	nonce, err := hex.DecodeString(strings.TrimPrefix(key, "assozeta:go:readiness:"))
	if err != nil || len(nonce) != 16 {
		t.Fatalf("want 128-bit nonce: %q", key)
	}
	script, ok := args[1].(string)
	if !ok || script == "" {
		t.Fatal("missing readiness script")
	}
	return script, key
}

func TestRedisProbeSuccessUsesOneEvalAndUniqueKeys(t *testing.T) {
	type requestKey struct{}
	ctx := context.WithValue(context.Background(), requestKey{}, "request")
	seen := make(map[string]bool)
	var script string
	client, hook := readinessTestClient(t, func(got context.Context, cmd redis.Cmder) error {
		if got != ctx {
			t.Fatal("request context was replaced")
		}
		currentScript, key := readinessEvalArgs(t, cmd)
		if seen[key] {
			t.Fatal("readiness nonce reused")
		}
		seen[key] = true
		if script != "" && script != currentScript {
			t.Fatal("script grows or changes per request")
		}
		script = currentScript
		cmd.(*redis.Cmd).SetVal("ok")
		return nil
	})
	probe := NewRedisProbe(client)
	for range 32 {
		if err := probe.Check(ctx); err != nil {
			t.Fatal(err)
		}
	}
	if hook.calls != 32 || len(seen) != 32 {
		t.Fatalf("unbounded work: %d commands, %d keys", hook.calls, len(seen))
	}
}

func TestRedisProbeRejectsMalformedReadback(t *testing.T) {
	for _, value := range []any{"wrong", "", nil, int64(1), true, []any{"ok"}} {
		t.Run(fmt.Sprintf("%T/%v", value, value), func(t *testing.T) {
			client, hook := readinessTestClient(t, func(_ context.Context, cmd redis.Cmder) error {
				readinessEvalArgs(t, cmd)
				cmd.(*redis.Cmd).SetVal(value)
				return nil
			})
			if err := NewRedisProbe(client).Check(context.Background()); err == nil {
				t.Fatalf("malformed response accepted: %#v", value)
			}
			if hook.calls != 1 {
				t.Fatalf("malformed response retried: %d", hook.calls)
			}
		})
	}
}

func TestRedisProbePropagatesFailuresWithoutFallback(t *testing.T) {
	for _, failure := range []error{
		redis.Nil,
		errors.New("NOPERM EVAL denied"),
		errors.New("ERR SET denied in script"),
		errors.New("ERR GET denied in script"),
		errors.New("ERR DEL denied in script"),
		errors.New("READONLY replica"),
		errors.New("OOM command not allowed"),
		errors.New("connection lost after write"),
		context.Canceled,
		context.DeadlineExceeded,
	} {
		t.Run(failure.Error(), func(t *testing.T) {
			client, hook := readinessTestClient(t, func(_ context.Context, cmd redis.Cmder) error {
				readinessEvalArgs(t, cmd)
				return failure
			})
			if err := NewRedisProbe(client).Check(context.Background()); !errors.Is(err, failure) {
				t.Fatalf("failure hidden: got %v, want %v", err, failure)
			}
			if hook.calls != 1 {
				t.Fatalf("failed probe retried/fell back: %d", hook.calls)
			}
		})
	}
}

func TestRedisProbeCancelledBeforeCommand(t *testing.T) {
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	expired, stop := context.WithDeadline(context.Background(), time.Unix(1, 0))
	defer stop()
	for _, ctx := range []context.Context{cancelled, expired} {
		client, hook := readinessTestClient(t, func(context.Context, redis.Cmder) error {
			t.Fatal("cancelled request reached Redis")
			return nil
		})
		if err := NewRedisProbe(client).Check(ctx); !errors.Is(err, ctx.Err()) {
			t.Fatalf("cancellation hidden: %v", err)
		}
		if hook.calls != 0 {
			t.Fatalf("cancelled request issued %d commands", hook.calls)
		}
	}
}

func TestRedisProbeInFlightDeadline(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	client, hook := readinessTestClient(t, func(got context.Context, cmd redis.Cmder) error {
		readinessEvalArgs(t, cmd)
		if got != ctx {
			t.Fatal("dependency deadline replaced")
		}
		<-got.Done()
		return got.Err()
	})
	start := time.Now()
	if err := NewRedisProbe(client).Check(ctx); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("deadline hidden: %v", err)
	}
	if time.Since(start) > time.Second || hook.calls != 1 {
		t.Fatalf("deadline/retry bound exceeded: %s, %d calls", time.Since(start), hook.calls)
	}
}

func TestRedisProbeDoesNotReportReadyAfterCancellation(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	client, hook := readinessTestClient(t, func(_ context.Context, cmd redis.Cmder) error {
		readinessEvalArgs(t, cmd)
		cancel()
		cmd.(*redis.Cmd).SetVal("ok")
		return nil
	})
	if err := NewRedisProbe(client).Check(ctx); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled result accepted: %v", err)
	}
	if hook.calls != 1 {
		t.Fatal("cancelled command retried")
	}
}

func TestRedisProbeRequiresBoundedClient(t *testing.T) {
	if err := NewRedisProbe(nil).Check(context.Background()); err == nil {
		t.Fatal("nil client accepted")
	}
	for _, options := range []redis.Options{
		{ContextTimeoutEnabled: true}, // default go-redis retries
		{MaxRetries: 1, ContextTimeoutEnabled: true},
		{MaxRetries: -1}, // deadlines ignored during I/O
	} {
		client := redis.NewClient(&options)
		hook := &readinessHook{process: func(context.Context, redis.Cmder) error {
			t.Fatal("unsafe client reached Redis")
			return nil
		}}
		client.AddHook(hook)
		if err := NewRedisProbe(client).Check(context.Background()); err == nil {
			t.Error("unbounded client accepted")
		}
		_ = client.Close()
	}
}

func TestRedisProbeTransportFailureIsNotRetried(t *testing.T) {
	// Let the real go-redis command/retry path run, but fail its dialer without
	// opening a socket. Process-hook counts alone cannot see internal retries.
	attempts := 0
	client := redis.NewClient(&redis.Options{
		MaxRetries: -1, ContextTimeoutEnabled: true, PoolSize: 2,
		Dialer: func(context.Context, string, string) (net.Conn, error) {
			attempts++
			return nil, io.EOF // Retryable by go-redis when retries are enabled.
		},
	})
	defer client.Close()
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := NewRedisProbe(client).Check(ctx); !errors.Is(err, io.EOF) {
		t.Fatalf("transport failure hidden: %v", err)
	}
	if attempts != 1 {
		t.Fatalf("hidden transport retries: %d attempts", attempts)
	}
}
