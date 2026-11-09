package service

import (
	"context"
	"errors"
	"reflect"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/channels"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type relayGoRealtimeFake struct {
	absent  bool
	pending []domain.RelayGoRealtimeEvent
	updates []string
	batches int
	fail    error
}

func (f *relayGoRealtimeFake) Present(context.Context) (bool, error) { return !f.absent, nil }

func (f *relayGoRealtimeFake) Transaction(ctx context.Context, fn func(domain.RelayGoRealtimeClaim) error) error {
	f.batches++
	return fn(f)
}

func (f *relayGoRealtimeFake) Pending(_ context.Context, limit int) ([]domain.RelayGoRealtimeEvent, error) {
	if limit != RelayGoRealtimeLimit {
		return nil, errors.New("limit")
	}
	rows := f.pending
	f.pending = nil
	return rows, nil
}

func (f *relayGoRealtimeFake) Delivered(_ context.Context, id int64) error {
	f.updates = append(f.updates, "delivered "+string(rune('0'+id)))
	return f.fail
}

func (f *relayGoRealtimeFake) Failed(_ context.Context, id int64, code string) error {
	f.updates = append(f.updates, "failed "+string(rune('0'+id))+" "+code)
	return f.fail
}

func (f *relayGoRealtimeFake) Retry(_ context.Context, id int64) error {
	f.updates = append(f.updates, "retry "+string(rune('0'+id)))
	return f.fail
}

type relayGoRealtimeSenderFake struct {
	sent []string
	err  error
}

func (s *relayGoRealtimeSenderFake) GroupSend(_ context.Context, group string, message channels.Dict) error {
	if s.err != nil {
		return s.err
	}
	s.sent = append(s.sent, group+" "+message[0].Value.(string))
	return nil
}

func TestRelayGoRealtimeOnceOutcomes(t *testing.T) {
	target := "00000000-0000-0000-0000-000000000001"
	store := &relayGoRealtimeFake{pending: []domain.RelayGoRealtimeEvent{
		{ID: 1, Version: 1, Kind: "staff_board_changed", Target: target},
		{ID: 2, Version: 1, Kind: "future_event", Target: target},
		{ID: 3, Version: 2, Kind: "staff_board_changed", Target: target},
		{ID: 4, Version: 1, Kind: "staff_board_changed", Target: target, Attempts: 29},
	}}
	sender := &relayGoRealtimeSenderFake{}
	counts, err := NewRelayGoRealtime(store, sender, nil).Once(context.Background())
	if err != nil || counts.String() != "{'delivered': 2, 'retry': 0, 'failed': 2}" {
		t.Fatalf("counts %v %v", counts, err)
	}
	want := []string{"delivered 1", "failed 2 unsupported_event", "failed 3 unsupported_event", "delivered 4"}
	if !reflect.DeepEqual(store.updates, want) || len(sender.sent) != 2 || sender.sent[0] != "staff_board_"+target+" staff_board_changed" {
		t.Fatalf("updates %v sent %v", store.updates, sender.sent)
	}
}

func TestRelayGoRealtimeRetryThenExhaust(t *testing.T) {
	store := &relayGoRealtimeFake{pending: []domain.RelayGoRealtimeEvent{
		{ID: 1, Version: 1, Kind: "staff_board_changed", Target: "t", Attempts: 0},
		{ID: 2, Version: 1, Kind: "staff_board_changed", Target: "t", Attempts: 28},
		{ID: 3, Version: 1, Kind: "staff_board_changed", Target: "t", Attempts: 29},
	}}
	counts, err := NewRelayGoRealtime(store, &relayGoRealtimeSenderFake{err: errors.New("down")}, nil).Once(context.Background())
	if err != nil || counts != (RelayGoRealtimeCounts{Retry: 2, Failed: 1}) {
		t.Fatalf("counts %v %v", counts, err)
	}
	if want := []string{"retry 1", "retry 2", "failed 3 delivery_exhausted"}; !reflect.DeepEqual(store.updates, want) {
		t.Fatalf("updates %v", store.updates)
	}
}

func TestRelayGoRealtimeAbsentOutboxAndErrors(t *testing.T) {
	store := &relayGoRealtimeFake{absent: true}
	counts, err := NewRelayGoRealtime(store, &relayGoRealtimeSenderFake{}, nil).Once(context.Background())
	if err != nil || counts.String() != "{'delivered': 0, 'retry': 0, 'failed': 0}" || store.batches != 0 {
		t.Fatalf("absent %v %v %d", counts, err, store.batches)
	}
	broken := &relayGoRealtimeFake{fail: errors.New("update"), pending: []domain.RelayGoRealtimeEvent{{ID: 1, Version: 1, Kind: "x"}}}
	if _, err := NewRelayGoRealtime(broken, &relayGoRealtimeSenderFake{}, nil).Once(context.Background()); err == nil {
		t.Fatal("update error must be returned")
	}
}

// drainStore yields one batch with progress per entry of progress, then nothing.
type drainStore struct {
	relayGoRealtimeFake
	progress []domain.RelayGoRealtimeEvent
	runs     atomic.Int32
}

func (d *drainStore) Transaction(ctx context.Context, fn func(domain.RelayGoRealtimeClaim) error) error {
	d.runs.Add(1)
	if len(d.progress) > 0 {
		d.pending = d.progress[:1]
		d.progress = d.progress[1:]
	}
	return fn(d)
}

func TestRelayGoRealtimeRunDrainsWhileProgressing(t *testing.T) {
	store := &drainStore{progress: []domain.RelayGoRealtimeEvent{
		{ID: 1, Version: 1, Kind: "staff_board_changed", Target: "t"},
		{ID: 2, Version: 1, Kind: "unknown", Target: "t"},
	}}
	ctx, cancel := context.WithCancel(context.Background())
	wake := make(chan struct{}, 1)
	done := make(chan error, 1)
	go func() { done <- NewRelayGoRealtime(store, &relayGoRealtimeSenderFake{}, nil).Run(ctx, time.Hour, wake) }()
	deadline := time.Now().Add(5 * time.Second)
	for store.runs.Load() < 3 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	// Two progressing batches and the empty one, then the loop waits.
	time.Sleep(20 * time.Millisecond)
	if store.runs.Load() != 3 {
		t.Fatalf("batches %d", store.runs.Load())
	}
	wake <- struct{}{}
	for store.runs.Load() < 4 && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	cancel()
	if err := <-done; !errors.Is(err, context.Canceled) || store.runs.Load() != 4 {
		t.Fatalf("run %v batches %d", err, store.runs.Load())
	}
}

func TestRelayGoRealtimeRunWaitsAfterRetry(t *testing.T) {
	store := &relayGoRealtimeFake{pending: []domain.RelayGoRealtimeEvent{{ID: 1, Version: 1, Kind: "staff_board_changed", Target: "t"}}}
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	err := NewRelayGoRealtime(store, &relayGoRealtimeSenderFake{err: errors.New("down")}, nil).Run(ctx, time.Hour, nil)
	if !errors.Is(err, context.DeadlineExceeded) || store.batches != 1 {
		t.Fatalf("run %v batches %d", err, store.batches)
	}
}
