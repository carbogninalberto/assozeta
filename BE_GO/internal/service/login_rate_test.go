package service

import (
	"context"
	"errors"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"testing"
	"time"
)

type rateCounterStub struct {
	key   string
	count int64
	err   error
	calls int
}

func (s *rateCounterStub) IncrementLoginRate(_ context.Context, key string) (int64, error) {
	s.calls++
	s.key = key
	return s.count, s.err
}

func TestLoginRateThresholdAndFailures(t *testing.T) {
	for _, tc := range []struct {
		group   string
		count   int64
		blocked bool
	}{
		{"login-ip", 1, false}, {"login-ip", 20, false}, {"login-ip", 21, true},
		{"login-account", 1, false}, {"login-account", 5, false}, {"login-account", 6, true},
	} {
		counter := &rateCounterStub{count: tc.count}
		gate := NewLoginRate(counter, func() time.Time { return time.Unix(1800000000, 0) })
		err := gate.Check(context.Background(), tc.group, "fixture")
		if errors.Is(err, ErrLoginRateLimited) != tc.blocked || (!tc.blocked && err != nil) || counter.calls != 1 {
			t.Fatalf("threshold %s/%d: %v", tc.group, tc.count, err)
		}
	}
	counter := &rateCounterStub{err: domain.ErrLoginRateCounterMissing}
	gate := NewLoginRate(counter, time.Now)
	if !errors.Is(gate.Check(context.Background(), "login-ip", "fixture"), ErrLoginRateLimited) {
		t.Fatal("expired counter admitted")
	}
	counter.err = errors.New("cache unavailable")
	if err := gate.Check(context.Background(), "login-ip", "fixture"); !errors.Is(err, counter.err) {
		t.Fatal("cache failure was hidden")
	}
	counter.calls = 0
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if !errors.Is(gate.Check(ctx, "login-ip", "fixture"), context.Canceled) || counter.calls != 0 {
		t.Fatal("cancelled request wrote a counter")
	}
	if gate.Check(context.Background(), "arbitrary-group", "fixture") == nil || counter.calls != 0 {
		t.Fatal("unknown group used cache")
	}
}
