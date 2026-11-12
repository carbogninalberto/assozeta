package queryobservation

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

const testRun = "0a11bc22-3333-4444-8555-666677778888"

type observedRecord struct {
	Type              string `json:"type"`
	RunID             string `json:"run_id"`
	RequestID         string `json:"request_id"`
	Sequence          uint64 `json:"sequence"`
	Complete          bool   `json:"complete"`
	QueryStarts       uint64 `json:"query_starts"`
	QueryEnds         uint64 `json:"query_ends"`
	QueryErrors       uint64 `json:"query_errors"`
	UnboundStarts     uint64 `json:"unbound_starts"`
	UnboundEnds       uint64 `json:"unbound_ends"`
	Violations        uint64 `json:"violations"`
	LateStarts        uint64 `json:"late_starts"`
	LateEnds          uint64 `json:"late_ends"`
	DuplicateEnds     uint64 `json:"duplicate_ends"`
	DuplicateFinishes uint64 `json:"duplicate_finishes"`
	ReusedContexts    uint64 `json:"reused_contexts"`
	MissingContexts   uint64 `json:"missing_contexts"`
	ForeignContexts   uint64 `json:"foreign_contexts"`
	ContextMismatches uint64 `json:"context_mismatches"`
	RequestStarts     uint64 `json:"request_starts"`
	RequestFinishes   uint64 `json:"request_finishes"`
	FinishCalls       uint64 `json:"finish_calls"`
	Summaries         uint64 `json:"summaries"`
	ActiveRequests    uint64 `json:"active_requests"`
	ActiveQueries     uint64 `json:"active_queries"`
}

func records(t *testing.T, b *bytes.Buffer) []observedRecord {
	t.Helper()
	var out []observedRecord
	d := json.NewDecoder(bytes.NewReader(b.Bytes()))
	for {
		var r observedRecord
		if err := d.Decode(&r); err == io.EOF {
			break
		} else if err != nil {
			t.Fatal(err)
		}
		out = append(out, r)
	}
	if len(out) == 0 || out[0].Type != "header" {
		t.Fatalf("missing header: %+v", out)
	}
	return out
}

func newTestObserver(t *testing.T) (*Observer, *bytes.Buffer) {
	t.Helper()
	b := new(bytes.Buffer)
	o, err := New(b, testRun)
	if err != nil {
		t.Fatal(err)
	}
	return o, b
}

func TestConcurrentRequestsUseContextsAcrossConnections(t *testing.T) {
	o, b := newTestObserver(t)
	const requests = 8
	var wg sync.WaitGroup
	ready := make(chan struct{}, requests)
	release := make(chan struct{})
	connections := []*pgx.Conn{{}, {}, {}}
	for i := 0; i < requests; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			ctx, _, finish := o.Begin(context.Background())
			// Two overlapping queries from the same request on different connections.
			a := o.TraceQueryStart(ctx, connections[i%3], pgx.TraceQueryStartData{})
			b := o.TraceQueryStart(ctx, connections[(i+1)%3], pgx.TraceQueryStartData{})
			ready <- struct{}{}
			<-release
			o.TraceQueryEnd(b, connections[(i+1)%3], pgx.TraceQueryEndData{})
			o.TraceQueryEnd(a, connections[i%3], pgx.TraceQueryEndData{})
			c := o.TraceQueryStart(ctx, connections[(i+2)%3], pgx.TraceQueryStartData{})
			o.TraceQueryEnd(c, connections[(i+2)%3], pgx.TraceQueryEndData{})
			finish()
		}(i)
	}
	for i := 0; i < requests; i++ {
		<-ready
	}
	close(release)
	wg.Wait()
	if err := o.Close(); err != nil {
		t.Fatal(err)
	}
	rr := records(t, b)
	if len(rr) != requests+2 {
		t.Fatalf("records=%d", len(rr))
	}
	seen := make(map[string]bool)
	for _, r := range rr[1 : len(rr)-1] {
		if r.Type != "request" || !r.Complete || r.Summaries != 1 || r.QueryStarts != 3 || r.QueryEnds != 3 || r.QueryErrors != 0 || r.Violations != 0 || r.RunID != testRun || seen[r.RequestID] || r.Sequence == 0 || r.Sequence > requests || !strings.HasPrefix(r.RequestID, testRun+"-") {
			t.Fatalf("bad request: %+v", r)
		}
		seen[r.RequestID] = true
	}
	f := rr[len(rr)-1]
	if f.Type != "footer" || !f.Complete || f.QueryStarts != 24 || f.QueryEnds != 24 || f.RequestStarts != 8 || f.RequestFinishes != 8 || f.FinishCalls != 8 || f.Summaries != 8 || f.ActiveQueries != 0 || f.ActiveRequests != 0 {
		t.Fatalf("bad footer: %+v", f)
	}
	before := b.Len()
	if err := o.Close(); err != nil || b.Len() != before {
		t.Fatal("Close must be idempotent")
	}
}

func TestIncompleteAndInvalidEvents(t *testing.T) {
	for _, tc := range []struct {
		name  string
		run   func(*Observer, context.Context, func())
		check func(observedRecord) bool
	}{
		{"query_error", func(o *Observer, c context.Context, f func()) {
			q := o.TraceQueryStart(c, nil, pgx.TraceQueryStartData{})
			o.TraceQueryEnd(q, nil, pgx.TraceQueryEndData{Err: errors.New("SECRET_ERROR")})
			f()
		}, func(r observedRecord) bool { return r.QueryErrors == 1 }},
		{"missing_end", func(o *Observer, c context.Context, f func()) {
			o.TraceQueryStart(c, nil, pgx.TraceQueryStartData{})
			f()
		}, func(r observedRecord) bool { return r.QueryStarts == 1 && r.QueryEnds == 0 && r.ActiveQueries == 1 }},
		{"late_end", func(o *Observer, c context.Context, f func()) {
			q := o.TraceQueryStart(c, nil, pgx.TraceQueryStartData{})
			f()
			o.TraceQueryEnd(q, nil, pgx.TraceQueryEndData{})
		}, func(r observedRecord) bool { return r.LateEnds == 1 && r.QueryStarts == r.QueryEnds }},
		{"late_start", func(o *Observer, c context.Context, f func()) {
			f()
			q := o.TraceQueryStart(c, nil, pgx.TraceQueryStartData{})
			o.TraceQueryEnd(q, nil, pgx.TraceQueryEndData{})
		}, func(r observedRecord) bool { return r.LateStarts == 1 && r.LateEnds == 1 }},
		{"duplicate_end", func(o *Observer, c context.Context, f func()) {
			q := o.TraceQueryStart(c, nil, pgx.TraceQueryStartData{})
			o.TraceQueryEnd(q, nil, pgx.TraceQueryEndData{})
			o.TraceQueryEnd(q, nil, pgx.TraceQueryEndData{})
			f()
		}, func(r observedRecord) bool { return r.DuplicateEnds == 1 && r.QueryEnds == 2 && r.ActiveQueries == 0 }},
		{"duplicate_finish", func(o *Observer, c context.Context, f func()) { f(); f() }, func(r observedRecord) bool {
			return r.DuplicateFinishes == 1 && r.FinishCalls == 2 && r.RequestFinishes == 1 && r.Summaries == 1
		}},
		{"missing_query_token", func(o *Observer, c context.Context, f func()) { o.TraceQueryEnd(c, nil, pgx.TraceQueryEndData{}); f() }, func(r observedRecord) bool { return r.UnboundEnds == 1 && r.MissingContexts == 1 && r.QueryEnds == 1 }},
		{"unbound_query", func(o *Observer, c context.Context, f func()) {
			q := o.TraceQueryStart(context.Background(), nil, pgx.TraceQueryStartData{})
			o.TraceQueryEnd(q, nil, pgx.TraceQueryEndData{})
			f()
		}, func(r observedRecord) bool {
			return r.UnboundStarts == 1 && r.UnboundEnds == 1 && r.QueryStarts == 1 && r.QueryEnds == 1
		}},
		{"reused_query_context", func(o *Observer, c context.Context, f func()) {
			a := o.TraceQueryStart(c, nil, pgx.TraceQueryStartData{})
			b := o.TraceQueryStart(a, nil, pgx.TraceQueryStartData{})
			o.TraceQueryEnd(b, nil, pgx.TraceQueryEndData{})
			o.TraceQueryEnd(a, nil, pgx.TraceQueryEndData{})
			f()
		}, func(r observedRecord) bool { return r.ReusedContexts == 1 }},
		{"reused_request_context", func(o *Observer, c context.Context, f func()) { _, _, g := o.Begin(c); g(); f() }, func(r observedRecord) bool { return r.ReusedContexts == 1 && r.RequestStarts == 2 && r.Summaries == 2 }},
		{"unfinished_request", func(o *Observer, c context.Context, f func()) {}, func(r observedRecord) bool {
			return r.RequestFinishes == 0 && r.Summaries == 0 && r.ActiveRequests == 1
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			o, b := newTestObserver(t)
			ctx, _, finish := o.Begin(context.Background())
			tc.run(o, ctx, finish)
			if err := o.Close(); !errors.Is(err, ErrIncomplete) {
				t.Fatalf("Close=%v", err)
			}
			rr := records(t, b)
			f := rr[len(rr)-1]
			if f.Type != "footer" || f.Complete || !tc.check(f) {
				t.Fatalf("bad footer: %+v", f)
			}
			if tc.name == "late_end" && rr[1].Complete {
				t.Fatal("late end repaired premature summary")
			}
			if strings.Contains(b.String(), "SECRET_ERROR") {
				t.Fatal("raw error leaked")
			}
		})
	}
}

func TestForeignAndWrongContextsStayWithOriginalObserver(t *testing.T) {
	a, ab := newTestObserver(t)
	b, bb := newTestObserver(t)
	ac, _, af := a.Begin(context.Background())
	bc, _, bf := b.Begin(context.Background())
	q := a.TraceQueryStart(ac, nil, pgx.TraceQueryStartData{})
	b.TraceQueryEnd(q, nil, pgx.TraceQueryEndData{})
	u := b.TraceQueryStart(ac, nil, pgx.TraceQueryStartData{})
	b.TraceQueryEnd(u, nil, pgx.TraceQueryEndData{})
	// A valid query token with a different current request must be flagged.
	wrong := context.WithValue(q, requestKey{}, bc.Value(requestKey{}))
	a.TraceQueryEnd(wrong, nil, pgx.TraceQueryEndData{})
	af()
	bf()
	if !errors.Is(a.Close(), ErrIncomplete) || !errors.Is(b.Close(), ErrIncomplete) {
		t.Fatal("foreign contexts accepted")
	}
	ar := records(t, ab)
	br := records(t, bb)
	if ar[len(ar)-1].ContextMismatches != 1 || br[len(br)-1].ForeignContexts < 2 || br[len(br)-1].UnboundStarts != 1 || br[len(br)-1].UnboundEnds != 2 {
		t.Fatalf("wrong counters: %+v %+v", ar, br)
	}
}

func TestContextAndPrivacy(t *testing.T) {
	o, b := newTestObserver(t)
	type parentKey struct{}
	parent, cancel := context.WithDeadline(context.WithValue(context.Background(), parentKey{}, "PRIVATE_CONTEXT"), time.Now().Add(time.Hour))
	defer cancel()
	ctx, _, finish := o.Begin(parent)
	if a, _ := parent.Deadline(); func() bool { z, ok := ctx.Deadline(); return !ok || z != a }() || ctx.Value(parentKey{}) != "PRIVATE_CONTEXT" {
		t.Fatal("parent context changed")
	}
	cancel()
	if !errors.Is(ctx.Err(), context.Canceled) {
		t.Fatal("cancellation lost")
	}
	q := o.TraceQueryStart(ctx, nil, pgx.TraceQueryStartData{SQL: "SQL_SECRET", Args: []any{"ARG_SECRET"}})
	if q.Err() != context.Canceled {
		t.Fatal("trace context lost cancellation")
	}
	o.TraceQueryEnd(q, nil, pgx.TraceQueryEndData{Err: errors.New("ERROR_SECRET")})
	finish()
	_ = o.Close()
	for _, secret := range []string{"SQL_SECRET", "ARG_SECRET", "ERROR_SECRET", "PRIVATE_CONTEXT"} {
		if strings.Contains(b.String(), secret) {
			t.Fatalf("leaked %s", secret)
		}
	}
}

type failingWriter struct {
	calls  int
	failAt int
	short  bool
	err    error
	bytes.Buffer
}

func (w *failingWriter) Write(p []byte) (int, error) {
	w.calls++
	if w.calls == w.failAt {
		if w.short {
			return w.Buffer.Write(p[:len(p)-1])
		}
		return 0, w.err
	}
	return w.Buffer.Write(p)
}

func TestSinkFailureIsStickyAndNotRetried(t *testing.T) {
	for _, short := range []bool{false, true} {
		for _, at := range []int{1, 2, 3} {
			w := &failingWriter{failAt: at, short: short, err: errors.New("sink unavailable")}
			o, err := New(w, testRun)
			want := w.err
			if short {
				want = io.ErrShortWrite
			}
			if at == 1 {
				if !errors.Is(err, want) || o != nil || w.calls != 1 {
					t.Fatalf("New=%v calls=%d", err, w.calls)
				}
				continue
			}
			if err != nil {
				t.Fatal(err)
			}
			_, _, f := o.Begin(context.Background())
			f()
			if err = o.Close(); !errors.Is(err, want) {
				t.Fatalf("Close=%v", err)
			}
			_ = o.Close()
			if w.calls != at {
				t.Fatalf("retried failed sink: %d", w.calls)
			}
		}
	}
}

func TestOutputLimit(t *testing.T) {
	o, b := newTestObserver(t)
	// Internal test seam lowers the same production hard cap without 64 MiB allocation.
	o.limit = int64(b.Len() + 1)
	_, _, f := o.Begin(context.Background())
	f()
	if err := o.Close(); !errors.Is(err, ErrLimit) {
		t.Fatalf("Close=%v", err)
	}
	if int64(b.Len()) > o.limit || len(records(t, b)) != 1 {
		t.Fatal("cap overflow or successful footer")
	}
}

func TestPostCloseEventsInvalidateFinalFooter(t *testing.T) {
	for _, event := range []string{"begin", "start", "end", "finish"} {
		t.Run(event, func(t *testing.T) {
			o, b := newTestObserver(t)
			ctx, _, f := o.Begin(context.Background())
			f()
			if err := o.Close(); err != nil {
				t.Fatal(err)
			}
			switch event {
			case "begin":
				o.Begin(context.Background())
			case "start":
				o.TraceQueryStart(ctx, nil, pgx.TraceQueryStartData{})
			case "end":
				o.TraceQueryEnd(ctx, nil, pgx.TraceQueryEndData{})
			case "finish":
				f()
			}
			if err := o.Close(); !errors.Is(err, ErrClosed) {
				t.Fatalf("Close=%v", err)
			}
			rr := records(t, b)
			if rr[len(rr)-1].Type != "invalidated" || rr[len(rr)-1].Complete {
				t.Fatal("post-close work left valid final footer")
			}
		})
	}
}

func TestConcurrentLifecycleAndClose(t *testing.T) {
	o, b := newTestObserver(t)
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			c, _, f := o.Begin(context.Background())
			q := o.TraceQueryStart(c, nil, pgx.TraceQueryStartData{})
			o.TraceQueryEnd(q, nil, pgx.TraceQueryEndData{})
			f()
			f()
			_ = o.Close()
		}()
	}
	wg.Wait()
	if err := o.Close(); err == nil {
		t.Fatal("invalid lifecycle succeeded")
	}
	rr := records(t, b)
	last := rr[len(rr)-1]
	if last.Type == "footer" && last.Complete {
		t.Fatal("invalid lifecycle has complete footer")
	}
}

func TestNewRejectsInvalidRunIDAndNilWriter(t *testing.T) {
	for _, run := range []string{"", "PRIVATE_RUN_LABEL", "\nSQL_SECRET"} {
		b := new(bytes.Buffer)
		if _, err := New(b, run); err == nil || b.Len() != 0 || strings.Contains(err.Error(), run) && run != "" {
			t.Fatal("invalid UUID accepted or echoed")
		}
	}
	if _, err := New(nil, testRun); err == nil {
		t.Fatal("nil sink accepted")
	}
}

func TestUnknownEndsAndErrorsRemainVisible(t *testing.T) {
	o, b := newTestObserver(t)
	c, _, f := o.Begin(context.Background())
	o.TraceQueryEnd(c, nil, pgx.TraceQueryEndData{Err: errors.New("UNKNOWN_ERROR_SECRET")})
	f()
	o.TraceQueryEnd(context.Background(), nil, pgx.TraceQueryEndData{Err: errors.New("UNBOUND_ERROR_SECRET")})
	if !errors.Is(o.Close(), ErrIncomplete) {
		t.Fatal("unknown ends succeeded")
	}
	rr := records(t, b)
	r, footer := rr[1], rr[len(rr)-1]
	if r.Complete || r.QueryStarts != 0 || r.QueryEnds != 1 || r.QueryErrors != 1 || r.UnboundEnds != 1 || r.MissingContexts != 1 {
		t.Fatalf("unknown request end lost: %+v", r)
	}
	if footer.QueryEnds != 2 || footer.QueryErrors != 2 || footer.UnboundEnds != 2 || footer.MissingContexts != 2 {
		t.Fatalf("unknown global ends lost: %+v", footer)
	}
	if strings.Contains(b.String(), "ERROR_SECRET") {
		t.Fatal("unknown error leaked")
	}
}

func TestConcurrentDuplicateEndsAndFinishes(t *testing.T) {
	o, b := newTestObserver(t)
	c, _, f := o.Begin(context.Background())
	q := o.TraceQueryStart(c, nil, pgx.TraceQueryStartData{})
	var wg sync.WaitGroup
	for i := 0; i < 16; i++ {
		wg.Add(2)
		go func() {
			defer wg.Done()
			o.TraceQueryEnd(q, nil, pgx.TraceQueryEndData{Err: errors.New("DUPLICATE_ERROR_SECRET")})
		}()
		go func() { defer wg.Done(); f() }()
	}
	wg.Wait()
	if !errors.Is(o.Close(), ErrIncomplete) {
		t.Fatal("duplicates succeeded")
	}
	rr := records(t, b)
	footer := rr[len(rr)-1]
	if len(rr) != 3 || rr[1].Complete || footer.QueryStarts != 1 || footer.QueryEnds != 16 || footer.QueryErrors != 16 || footer.DuplicateEnds != 15 || footer.DuplicateFinishes != 15 || footer.FinishCalls != 16 || footer.RequestFinishes != 1 || footer.Summaries != 1 || footer.ActiveQueries != 0 {
		t.Fatalf("duplicates lost: %+v", footer)
	}
}

type unformattableSecret struct{}

func (unformattableSecret) String() string { panic("argument formatting must never occur") }
func (unformattableSecret) Error() string  { panic("error formatting must never occur") }

func TestSensitiveInputsAreNeverFormatted(t *testing.T) {
	o, b := newTestObserver(t)
	c, _, f := o.Begin(context.Background())
	q := o.TraceQueryStart(c, nil, pgx.TraceQueryStartData{SQL: "SQL_SECRET", Args: []any{unformattableSecret{}}})
	o.TraceQueryEnd(q, nil, pgx.TraceQueryEndData{CommandTag: pgconn.NewCommandTag("TAG_SECRET"), Err: unformattableSecret{}})
	f()
	if !errors.Is(o.Close(), ErrIncomplete) {
		t.Fatal("query error not counted")
	}
	for _, s := range []string{"SQL_SECRET", "TAG_SECRET"} {
		if strings.Contains(b.String(), s) {
			t.Fatalf("leaked %s", s)
		}
	}
}

type parentOwnedWriter struct {
	bytes.Buffer
	closed bool
}

func (w *parentOwnedWriter) Close() error { w.closed = true; return nil }

func TestFooterCapAndParentWriterOwnership(t *testing.T) {
	w := new(parentOwnedWriter)
	o, err := New(w, testRun)
	if err != nil {
		t.Fatal(err)
	}
	_, _, f := o.Begin(context.Background())
	f()
	before := w.Len()
	o.limit = int64(before + 1)
	if !errors.Is(o.Close(), ErrLimit) || w.Len() != before || w.closed {
		t.Fatal("footer cap or ownership violated")
	}
	if rr := records(t, &w.Buffer); rr[len(rr)-1].Type == "footer" {
		t.Fatal("cap wrote successful footer")
	}
	w = new(parentOwnedWriter)
	o, err = New(w, testRun)
	if err != nil {
		t.Fatal(err)
	}
	if err = o.Close(); err != nil || w.closed {
		t.Fatalf("observer closed parent writer: %v", err)
	}
}

func TestRunCanonicalizationAndMonotonicIDs(t *testing.T) {
	var b bytes.Buffer
	o, err := New(&b, strings.ToUpper(testRun))
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{testRun + "-1", testRun + "-2"} {
		_, id, f := o.Begin(context.Background())
		if id != want {
			t.Fatalf("id=%s want=%s", id, want)
		}
		f()
	}
	if err = o.Close(); err != nil {
		t.Fatal(err)
	}
	for _, r := range records(t, &b) {
		if r.RunID != testRun {
			t.Fatal("noncanonical run")
		}
	}
}
