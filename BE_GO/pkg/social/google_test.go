package social

import (
	"context"
	"errors"
	"io"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestGoogleTransportFailureBoundaryAndBodyClosure(t *testing.T) {
	for _, tc := range []struct {
		name, certBody string
		status         int
		wantError      bool
		calls          int
	}{
		{"certificate transport status", "{}", 503, true, 1},
		{"invalid certificate JSON falls back", "invalid", 200, false, 2},
		{"invalid token falls back", "{}", 200, false, 2},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var calls int
			var closed atomic.Int32
			client := newTestGoogle(httpCall(func(r *http.Request) (*http.Response, error) {
				calls++
				duration := 120 * time.Second
				body, status := tc.certBody, tc.status
				if calls > 1 {
					duration = 10 * time.Second
					body = "{}"
					status = 401
				}
				deadline, ok := r.Context().Deadline()
				if !ok || time.Until(deadline) > duration || time.Until(deadline) < duration-time.Second {
					t.Error("provider phase deadline differs")
				}
				return &http.Response{StatusCode: status, Body: closeReader{strings.NewReader(body), &closed}}, nil
			}), time.Now, 1)
			info, valid, err := client.Verify(context.Background(), "access-token", "client")
			if info != nil || valid || (err != nil) != tc.wantError || calls != tc.calls || int(closed.Load()) != calls {
				t.Fatal("Google failure boundary or closure differs")
			}
		})
	}
	var closed atomic.Int32
	client := newTestGoogle(httpCall(func(*http.Request) (*http.Response, error) {
		return &http.Response{Body: closeReader{strings.NewReader(""), &closed}}, errors.New("sensitive upstream detail")
	}), time.Now, 1)
	if _, valid, err := client.Verify(context.Background(), "token", "client"); valid || !errors.Is(err, ErrGoogleProvider) || closed.Load() != 1 {
		t.Fatal("transport details leaked or body retained")
	}
}

func TestGoogleBoundCancellationAndReuse(t *testing.T) {
	var calls, active, peak atomic.Int32
	entered := make(chan struct{}, 3)
	client := newTestGoogle(httpCall(func(r *http.Request) (*http.Response, error) {
		calls.Add(1)
		n := active.Add(1)
		defer active.Add(-1)
		for old := peak.Load(); n > old && !peak.CompareAndSwap(old, n); old = peak.Load() {
		}
		entered <- struct{}{}
		<-r.Context().Done()
		return nil, r.Context().Err()
	}), time.Now, 2)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var wg sync.WaitGroup
	for i := 0; i < 2; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			info, valid, err := client.Verify(ctx, "token", "client")
			if info != nil || valid || !errors.Is(err, context.Canceled) {
				t.Error("running cancellation lost")
			}
		}()
	}
	for i := 0; i < 2; i++ {
		select {
		case <-entered:
		case <-time.After(2 * time.Second):
			t.Fatal("provider did not start")
		}
	}
	waiting, stop := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer stop()
	if _, valid, err := client.Verify(waiting, "token", "client"); valid || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("queued cancellation lost")
	}
	if calls.Load() != 2 {
		t.Fatal("provider admission exceeded bound")
	}
	cancel()
	wg.Wait()
	retry, done := context.WithCancel(context.Background())
	defer done()
	finished := make(chan error, 1)
	go func() { _, _, err := client.Verify(retry, "token", "client"); finished <- err }()
	select {
	case <-entered:
	case <-time.After(2 * time.Second):
		t.Fatal("slot not released")
	}
	done()
	if !errors.Is(<-finished, context.Canceled) || peak.Load() != 2 || calls.Load() != 3 || active.Load() != 0 {
		t.Fatal("bound or slot reuse failed")
	}
}

func TestGoogleResponseReadBoundAndDisabledProvider(t *testing.T) {
	body := &countedAppleBody{reader: strings.NewReader(strings.Repeat(" ", MaxGoogleResponseBytes+4096))}
	client := newTestGoogle(httpCall(func(*http.Request) (*http.Response, error) { return &http.Response{StatusCode: 200, Body: body}, nil }), time.Now, 1)
	if _, valid, err := client.Verify(context.Background(), "token", "client"); valid || !errors.Is(err, ErrGoogleProvider) {
		t.Fatal("oversized certificate response accepted")
	}
	if body.read != MaxGoogleResponseBytes+1 || !body.closed {
		t.Fatal("certificate response read unbounded")
	}
	client = newTestGoogle(httpCall(func(*http.Request) (*http.Response, error) { t.Fatal("disabled provider contacted"); return nil, nil }), time.Now, 1)
	for _, audience := range []any{nil, "", false, []any{}, map[string]any{}} {
		if info, valid, err := client.Verify(context.Background(), "token", audience); info != nil || valid || err != nil {
			t.Fatal("disabled provider admitted")
		}
	}
}

func TestGoogleFallbackNetworkFailureReturnsNoIdentity(t *testing.T) {
	calls := 0
	client := newTestGoogle(httpCall(func(*http.Request) (*http.Response, error) {
		calls++
		if calls == 1 {
			return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader("{}"))}, nil
		}
		return nil, errors.New("sensitive upstream detail")
	}), time.Now, 1)
	if info, valid, err := client.Verify(context.Background(), "access-token", "client"); info != nil || valid || err != nil || calls != 2 {
		t.Fatal("access fallback network failure escaped or returned identity")
	}
}

type googleRoundTrip func(*http.Request) (*http.Response, error)

func (f googleRoundTrip) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }
func newTestGoogle(client HTTPClient, now func() time.Time, concurrency int) *Google {
	return NewGoogle(client, googleRoundTrip(client.Do), now, concurrency)
}
