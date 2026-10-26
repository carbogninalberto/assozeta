package queryobservation

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

func driverRecords(t *testing.T, b *bytes.Buffer) []record {
	t.Helper()
	d := json.NewDecoder(bytes.NewReader(b.Bytes()))
	var rr []record
	for d.More() {
		var r record
		if err := d.Decode(&r); err != nil {
			t.Fatal(err)
		}
		rr = append(rr, r)
	}
	return rr
}

func TestDriverRequestAccountingAndContextSeparation(t *testing.T) {
	o, b := newTestObserver(t)
	const n = 16
	var wg sync.WaitGroup
	ready, release := make(chan struct{}, n), make(chan struct{})
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			ctx, _, finish := o.Begin(context.Background())
			_, connect := o.BeginDriverOperation(ctx, DriverOperationConnect)
			connect(nil)
			for j := 0; j < 3; j++ {
				r, reset := o.BeginDriverOperation(ctx, DriverOperationReset)
				if j == 0 {
					ready <- struct{}{}
					<-release
				}
				o.ObserveResetProbe(r)
				reset(nil)
				q := o.TraceQueryStart(ctx, nil, pgx.TraceQueryStartData{})
				o.TraceQueryEnd(q, nil, pgx.TraceQueryEndData{})
			}
			_, ping := o.BeginDriverOperation(ctx, DriverOperationPing)
			ping(nil)
			finish()
		}()
	}
	for i := 0; i < n; i++ {
		<-ready
	}
	close(release)
	wg.Wait()
	if err := o.Close(); err != nil {
		t.Fatal(err)
	}
	rr := driverRecords(t, b)
	for _, r := range rr[1 : len(rr)-1] {
		if !r.Complete || r.ConnectStarts != 1 || r.ConnectEnds != 1 || r.ResetStarts != 3 || r.ResetEnds != 3 || r.ResetProbes != 3 || r.PingStarts != 1 || r.PingEnds != 1 || r.QueryStarts != 3 || r.DriverErrors != 0 || r.ActiveDriverOperations != 0 {
			t.Fatalf("request: %+v", r)
		}
	}
	f := rr[len(rr)-1]
	if !f.Complete || f.DriverStarts != 5*n || f.DriverEnds != 5*n || f.ResetProbes != 3*n || f.ActiveDriverOperations != 0 {
		t.Fatalf("footer: %+v", f)
	}
}

func TestDriverInvalidLifecycle(t *testing.T) {
	for _, name := range []string{"missing", "foreign", "mismatch", "duplicate_probe", "late_probe", "wrong_kind", "unknown_kind", "missing_finish", "duplicate_finish", "late_start", "late_finish", "reuse", "unbound_start", "foreign_start"} {
		t.Run(name, func(t *testing.T) {
			o, b := newTestObserver(t)
			ctx, _, finish := o.Begin(context.Background())
			r, reset := o.BeginDriverOperation(ctx, DriverOperationReset)
			switch name {
			case "missing":
				o.ObserveResetProbe(ctx)
			case "foreign":
				other, _ := newTestObserver(t)
				c, _, f := other.Begin(context.Background())
				c, end := other.BeginDriverOperation(c, DriverOperationReset)
				o.ObserveResetProbe(c)
				end(nil)
				f()
				if err := other.Close(); err != nil {
					t.Fatal(err)
				}
			case "mismatch":
				other, _, f := o.Begin(context.Background())
				o.ObserveResetProbe(context.WithValue(r, requestKey{}, other.Value(requestKey{})))
				f()
			case "duplicate_probe":
				o.ObserveResetProbe(r)
				o.ObserveResetProbe(r)
			case "late_probe":
				reset(nil)
				o.ObserveResetProbe(r)
			case "wrong_kind":
				c, end := o.BeginDriverOperation(ctx, DriverOperationPing)
				o.ObserveResetProbe(c)
				end(nil)
			case "unknown_kind":
				_, end := o.BeginDriverOperation(ctx, "PRIVATE_KIND")
				end(unformattableSecret{})
			case "missing_finish":
				finish()
			case "duplicate_finish":
				reset(nil)
				reset(nil)
			case "late_start":
				finish()
				_, end := o.BeginDriverOperation(ctx, DriverOperationConnect)
				end(nil)
			case "late_finish":
				finish()
			case "reuse":
				_, end := o.BeginDriverOperation(r, DriverOperationPing)
				end(nil)
			case "unbound_start":
				_, end := o.BeginDriverOperation(context.Background(), DriverOperationConnect)
				end(nil)
			case "foreign_start":
				other, _ := newTestObserver(t)
				c, _, f := other.Begin(context.Background())
				_, end := o.BeginDriverOperation(c, DriverOperationConnect)
				end(nil)
				f()
				_ = other.Close()
			}
			if name != "late_probe" && name != "duplicate_finish" && name != "missing_finish" {
				reset(nil)
			}
			if name != "missing_finish" && name != "late_start" && name != "late_finish" {
				finish()
			}
			if !errors.Is(o.Close(), ErrIncomplete) {
				t.Fatal("invalid lifecycle accepted")
			}
			rr := driverRecords(t, b)
			f := rr[len(rr)-1]
			if f.Complete || (name != "missing_finish" && f.DriverViolations == 0) {
				t.Fatalf("footer: %+v", f)
			}
			if name == "missing_finish" && f.ActiveDriverOperations != 1 {
				t.Fatalf("lost active operation: %+v", f)
			}
			if strings.Contains(b.String(), "PRIVATE_KIND") {
				t.Fatal("kind leaked")
			}
		})
	}
}

func TestDriverErrorsBeforeQueriesRemainVisible(t *testing.T) {
	o, b := newTestObserver(t)
	ctx, _, finish := o.Begin(context.Background())
	for _, kind := range []string{DriverOperationConnect, DriverOperationReset, DriverOperationReset, DriverOperationPing} {
		c, end := o.BeginDriverOperation(ctx, kind)
		if kind == DriverOperationReset {
			o.ObserveResetProbe(c)
		}
		end(unformattableSecret{})
	}
	finish()
	if !errors.Is(o.Close(), ErrIncomplete) {
		t.Fatal("driver errors accepted")
	}
	rr := driverRecords(t, b)
	for _, r := range rr[1:] {
		if r.Complete || r.DriverErrors != 4 || r.ConnectErrors != 1 || r.ResetErrors != 2 || r.PingErrors != 1 || r.QueryStarts != 0 || r.ResetProbes != 2 {
			t.Fatalf("errors: %+v", r)
		}
	}
}

func TestDriverContextPropagationAndPostClose(t *testing.T) {
	o, b := newTestObserver(t)
	type key struct{}
	parent, cancel := context.WithDeadline(context.WithValue(context.Background(), key{}, "SECRET"), time.Now().Add(time.Hour))
	ctx, _, finish := o.Begin(parent)
	c, end := o.BeginDriverOperation(ctx, DriverOperationReset)
	if d, _ := c.Deadline(); func() bool { p, ok := parent.Deadline(); return !ok || d != p }() || c.Value(key{}) != "SECRET" {
		t.Fatal("context changed")
	}
	cancel()
	if c.Err() != context.Canceled {
		t.Fatal("cancellation lost")
	}
	end(nil)
	finish()
	if err := o.Close(); err != nil {
		t.Fatal(err)
	}
	o.ObserveResetProbe(c)
	o.BeginDriverOperation(ctx, "SECRET_KIND")
	end(unformattableSecret{})
	if !errors.Is(o.Close(), ErrClosed) {
		t.Fatal("post-close work accepted")
	}
	rr := driverRecords(t, b)
	if rr[len(rr)-1].Type != "invalidated" || rr[len(rr)-1].Complete || strings.Contains(b.String(), "SECRET") {
		t.Fatal("invalid final stream")
	}
}

func TestDriverLifecycleCountersAndFinalizedSummary(t *testing.T) {
	for _, event := range []string{"start", "probe", "finish"} {
		t.Run(event, func(t *testing.T) {
			o, b := newTestObserver(t)
			ctx, _, finish := o.Begin(context.Background())
			r, end := o.BeginDriverOperation(ctx, DriverOperationReset)
			end(nil)
			finish()
			switch event {
			case "start":
				_, stop := o.BeginDriverOperation(ctx, DriverOperationPing)
				stop(nil)
			case "probe":
				o.ObserveResetProbe(r)
			case "finish":
				end(nil)
			}
			if !errors.Is(o.Close(), ErrIncomplete) {
				t.Fatal("late event accepted")
			}
			rr := driverRecords(t, b)
			if !rr[1].Complete || rr[len(rr)-1].Complete || rr[len(rr)-1].DriverViolations == 0 {
				t.Fatal("late work repaired summary or left valid footer")
			}
		})
	}
}

func TestDriverConcurrentDuplicateEventsReleaseOnlyOnce(t *testing.T) {
	o, b := newTestObserver(t)
	ctx, _, finish := o.Begin(context.Background())
	r, end := o.BeginDriverOperation(ctx, DriverOperationReset)
	var wg sync.WaitGroup
	for i := 0; i < 16; i++ {
		wg.Add(2)
		go func() { defer wg.Done(); o.ObserveResetProbe(r) }()
		go func() { defer wg.Done(); end(unformattableSecret{}) }()
	}
	wg.Wait()
	finish()
	if !errors.Is(o.Close(), ErrIncomplete) {
		t.Fatal("duplicate events accepted")
	}
	rr := driverRecords(t, b)
	f := rr[len(rr)-1]
	if f.DriverStarts != 1 || f.DriverEnds != 16 || f.DriverErrors != 16 || f.DuplicateEnds < 15 || f.ResetProbes != 16 || f.InvalidResetProbes < 15 || f.ActiveDriverOperations != 0 || f.DriverViolations < 30 {
		t.Fatalf("duplicates lost: %+v", f)
	}
}

func TestDriverUnboundCountersDoNotChangeQueryCounters(t *testing.T) {
	o, b := newTestObserver(t)
	r, end := o.BeginDriverOperation(context.Background(), DriverOperationReset)
	o.ObserveResetProbe(r)
	end(nil)
	if !errors.Is(o.Close(), ErrIncomplete) {
		t.Fatal("unbound work accepted")
	}
	rr := driverRecords(t, b)
	f := rr[len(rr)-1]
	if f.DriverUnboundStarts != 1 || f.DriverUnboundEnds != 1 || f.QueryStarts != 0 || f.QueryEnds != 0 || f.UnboundStarts != 0 || f.UnboundEnds != 0 || f.InvalidResetProbes != 1 || f.ActiveDriverOperations != 0 {
		t.Fatalf("counters: %+v", f)
	}
}

func TestDriverProbeMismatchDoesNotConsumeOriginalToken(t *testing.T) {
	o, b := newTestObserver(t)
	a, _, af := o.Begin(context.Background())
	c, _, cf := o.Begin(context.Background())
	r, end := o.BeginDriverOperation(a, DriverOperationReset)
	wrong := context.WithValue(r, requestKey{}, c.Value(requestKey{}))
	o.ObserveResetProbe(wrong)
	o.ObserveResetProbe(r)
	end(nil)
	af()
	cf()
	if !errors.Is(o.Close(), ErrIncomplete) {
		t.Fatal("mismatch accepted")
	}
	rr := driverRecords(t, b)
	f := rr[len(rr)-1]
	if f.ContextMismatches != 1 || f.ResetProbes != 2 || f.InvalidResetProbes != 1 || f.DuplicateEnds != 0 || f.ActiveDriverOperations != 0 || rr[1].ResetProbes != 2 || rr[2].ResetProbes != 0 {
		t.Fatalf("token accounting: %+v", rr)
	}
}

func TestDriverEachPostCloseEntryInvalidates(t *testing.T) {
	for _, event := range []string{"start", "probe", "finish"} {
		t.Run(event, func(t *testing.T) {
			o, b := newTestObserver(t)
			ctx, _, finish := o.Begin(context.Background())
			r, end := o.BeginDriverOperation(ctx, DriverOperationReset)
			end(nil)
			finish()
			if err := o.Close(); err != nil {
				t.Fatal(err)
			}
			switch event {
			case "start":
				o.BeginDriverOperation(ctx, DriverOperationConnect)
			case "probe":
				o.ObserveResetProbe(r)
			case "finish":
				end(nil)
			}
			if !errors.Is(o.Close(), ErrClosed) {
				t.Fatal("post-close event accepted")
			}
			rr := driverRecords(t, b)
			f := rr[len(rr)-1]
			if f.Type != "invalidated" || f.Complete || f.PostCloseEvents != 1 || f.DriverViolations != 1 {
				t.Fatalf("footer remains final: %+v", f)
			}
		})
	}
}

func TestDriverZeroValueRemainsUnusableAndErrorsSticky(t *testing.T) {
	var o Observer
	ctx, _, finish := o.Begin(context.Background())
	c, end := o.BeginDriverOperation(ctx, DriverOperationReset)
	o.ObserveResetProbe(c)
	end(nil)
	finish()
	if !errors.Is(o.Close(), ErrLimit) || !errors.Is(o.Close(), ErrLimit) {
		t.Fatal("zero value became usable or lost sticky sink error")
	}
}

func TestActiveRequestsAtStartConcurrentBarrierAndOutOfOrderFinishes(t *testing.T) {
	o, b := newTestObserver(t)
	const n = 8
	type started struct {
		sequence uint64
		release  chan struct{}
		done     chan struct{}
	}
	ready := make(chan started, n)
	for i := 0; i < n; i++ {
		go func() {
			ctx, _, finish := o.Begin(context.Background())
			s := started{sequence: ctx.Value(requestKey{}).(*request).sequence, release: make(chan struct{}), done: make(chan struct{})}
			ready <- s
			<-s.release
			finish()
			close(s.done)
		}()
	}
	all := make(map[uint64]started)
	for i := 0; i < n; i++ {
		s := <-ready
		all[s.sequence] = s
	}
	stop := func(sequence uint64) { s := all[sequence]; close(s.release); <-s.done }
	// Every Begin completed before any finish. Then finish out of sequence,
	// and start a ninth request after one finish, while seven remain active.
	stop(8)
	_, _, ninth := o.Begin(context.Background())
	for _, sequence := range []uint64{1, 7, 2, 6, 3, 5, 4} {
		stop(sequence)
	}
	ninth()
	if err := o.Close(); err != nil {
		t.Fatal(err)
	}
	rr := driverRecords(t, b)
	if len(rr) != n+3 || rr[0].ActiveRequestsAtStart != 0 || rr[len(rr)-1].ActiveRequestsAtStart != 0 {
		t.Fatalf("record shape: %+v", rr)
	}
	order := []uint64{8, 1, 7, 2, 6, 3, 5, 4, 9}
	for i, r := range rr[1 : len(rr)-1] {
		want := r.Sequence
		if want == 9 {
			want = 8
		}
		if r.Sequence != order[i] || !r.Complete || r.ActiveRequestsAtStart != want {
			t.Fatalf("mutable or wrong overlap snapshot: %+v", r)
		}
	}
}
