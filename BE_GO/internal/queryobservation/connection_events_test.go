package queryobservation

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"reflect"
	"strings"
	"sync"
	"testing"
	"time"
	"unsafe"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

// Deliberately fabricated inert handles: callback accounting only, NOT measured
// PostgreSQL evidence. Unsafe stays in tests; production has no PID injection API.
func syntheticField(target any, name string, value any) {
	f := reflect.ValueOf(target).Elem().FieldByName(name)
	reflect.NewAt(f.Type(), unsafe.Pointer(f.UnsafeAddr())).Elem().Set(reflect.ValueOf(value))
}
func syntheticConnection(pid uint32) *pgx.Conn {
	p := &pgconn.PgConn{}
	syntheticField(p, "pid", pid)
	c := &pgx.Conn{}
	syntheticField(c, "pgConn", p)
	return c
}

type connectionRow struct {
	Stream                 string `json:"stream"`
	Version                int    `json:"version"`
	Type                   string `json:"type"`
	RunID                  string `json:"run_id"`
	Sequence               uint64 `json:"sequence"`
	RequestID              string `json:"request_id"`
	QueryID                string `json:"query_id"`
	BackendPID             uint32 `json:"backend_pid"`
	Error                  bool   `json:"error"`
	Complete               bool   `json:"complete"`
	RequestStarts          uint64 `json:"request_starts"`
	RequestFinishes        uint64 `json:"request_finishes"`
	FinishCalls            uint64 `json:"finish_calls"`
	ActiveRequests         uint64 `json:"active_requests"`
	ActiveQueries          uint64 `json:"active_queries"`
	ActiveBindings         uint64 `json:"active_bindings"`
	ConnectionViolations   uint64 `json:"connection_violations"`
	ActiveDriverOperations uint64 `json:"active_driver_operations"`
	PostCloseEvents        uint64 `json:"post_close_events"`
	counters
}

func connectionRows(t *testing.T, b *bytes.Buffer) []connectionRow {
	t.Helper()
	var rows []connectionRow
	d := json.NewDecoder(bytes.NewReader(b.Bytes()))
	d.DisallowUnknownFields()
	for {
		var row connectionRow
		if err := d.Decode(&row); err == io.EOF {
			break
		} else if err != nil {
			t.Fatal(err)
		}
		if row.Stream != "query_connection" || row.Version != 1 || row.RunID != testRun || row.Sequence != uint64(len(rows)+1) {
			t.Fatalf("bad fixed schema/sequence: %+v", row)
		}
		rows = append(rows, row)
	}
	return rows
}
func connectionObserver(t *testing.T) (*Observer, *bytes.Buffer, *bytes.Buffer) {
	t.Helper()
	summary, events := new(bytes.Buffer), new(bytes.Buffer)
	o, err := NewWithConnectionEvents(summary, events, testRun)
	if err != nil {
		t.Fatal(err)
	}
	return o, summary, events
}

func TestConnectionEventsVisibleBeforeEndAndFinish(t *testing.T) {
	o, summary, events := connectionObserver(t)
	type clientKey struct{}
	ctx, id, finish := o.Begin(context.WithValue(context.Background(), clientKey{}, "SPOOF_CID_SECRET"))
	c := syntheticConnection(4312)
	q := o.TraceQueryStart(ctx, c, pgx.TraceQueryStartData{SQL: "SQL_SECRET", Args: []any{unformattableSecret{}}})
	rr := connectionRows(t, events)
	if len(rr) != 3 || rr[0].Type != "header" || rr[1].Type != "request_begin" || rr[2].Type != "query_start" || !rr[2].Complete || rr[2].RequestID != id || rr[2].BackendPID != 4312 || !strings.HasPrefix(rr[2].QueryID, testRun+"-q") || rr[2].ActiveBindings != 1 || len(records(t, summary)) != 1 {
		t.Fatalf("start not independently visible: %+v", rr)
	}
	o.TraceQueryEnd(q, c, pgx.TraceQueryEndData{CommandTag: pgconn.NewCommandTag("TAG_SECRET")})
	assertConnectionBindingDrained(t, o, q)
	finish()
	if err := o.Close(); err != nil {
		t.Fatal(err)
	}
	rr = connectionRows(t, events)
	if len(rr) != 6 || rr[3].Type != "query_end" || rr[3].QueryID != rr[2].QueryID || rr[3].BackendPID != 4312 || rr[3].RequestID != id || !rr[3].Complete || rr[4].Type != "request_finish" || rr[4].RequestID != id || rr[5].Type != "footer" || !rr[5].Complete || rr[5].ActiveBindings != 0 {
		t.Fatalf("lifetime not bound: %+v", rr)
	}
	for _, s := range []string{"SQL_SECRET", "TAG_SECRET", "SPOOF_CID_SECRET"} {
		if strings.Contains(events.String()+summary.String(), s) {
			t.Fatalf("leaked %s", s)
		}
	}
}

func TestConnectionEventsRejectInvalidLifecycles(t *testing.T) {
	for _, name := range []string{"nil", "empty", "zero", "different_connection", "same_pid_other_connection", "replacement", "pid_changed", "overlapping_connection", "overlapping_pid", "missing_context", "nil_context", "missing_token", "wrong_request", "foreign_start", "foreign_end", "reused_query", "reused_completed_query", "reused_request", "duplicate_end", "late_start", "late_end", "duplicate_finish", "missing_end", "missing_finish", "close_while_active"} {
		t.Run(name, func(t *testing.T) {
			o, _, events := connectionObserver(t)
			ctx, _, finish := o.Begin(context.Background())
			c := syntheticConnection(101)
			if name == "nil" {
				c = nil
			}
			if name == "empty" {
				c = &pgx.Conn{}
			}
			if name == "zero" {
				c = syntheticConnection(0)
			}
			startCtx := ctx
			if name == "missing_context" {
				startCtx = context.Background()
			}
			if name == "nil_context" {
				startCtx = nil
			}
			other, _, _ := connectionObserver(t)
			foreign, _, foreignFinish := other.Begin(context.Background())
			defer foreignFinish()
			if name == "foreign_start" {
				startCtx = foreign
			}
			if name == "late_start" {
				finish()
			}
			q := o.TraceQueryStart(startCtx, c, pgx.TraceQueryStartData{})
			endCtx, endConn := q, c
			switch name {
			case "different_connection":
				endConn = syntheticConnection(202)
			case "same_pid_other_connection":
				endConn = syntheticConnection(101)
			case "replacement":
				syntheticField(c, "pgConn", syntheticConnection(101).PgConn())
			case "pid_changed":
				syntheticField(c.PgConn(), "pid", uint32(202))
			case "overlapping_connection", "overlapping_pid", "reused_query", "reused_completed_query":
				c2, ctx2 := c, ctx
				if name == "overlapping_pid" {
					c2 = syntheticConnection(101)
				}
				if name == "reused_query" || name == "reused_completed_query" {
					ctx2 = q
				}
				if name == "reused_completed_query" {
					o.TraceQueryEnd(q, c, pgx.TraceQueryEndData{})
				}
				q2 := o.TraceQueryStart(ctx2, c2, pgx.TraceQueryStartData{})
				o.TraceQueryEnd(q2, c2, pgx.TraceQueryEndData{})
			case "missing_token":
				endCtx = ctx
			case "wrong_request":
				second, _, f := o.Begin(context.Background())
				defer f()
				endCtx = context.WithValue(q, requestKey{}, second.Value(requestKey{}))
			case "foreign_end":
				other.TraceQueryEnd(q, c, pgx.TraceQueryEndData{})
				if !errors.Is(other.Close(), ErrIncomplete) {
					t.Fatal("foreign end accepted")
				}
				return
			case "reused_request":
				_, _, f := o.Begin(ctx)
				f()
			case "late_end":
				finish()
			case "close_while_active":
				if !errors.Is(o.Close(), ErrIncomplete) {
					t.Fatal("active Close accepted")
				}
			}
			if name != "missing_end" {
				o.TraceQueryEnd(endCtx, endConn, pgx.TraceQueryEndData{})
			}
			if name == "duplicate_end" {
				o.TraceQueryEnd(q, c, pgx.TraceQueryEndData{})
			}
			if name != "missing_finish" {
				finish()
			}
			if name == "duplicate_finish" {
				finish()
			}
			if err := o.Close(); !errors.Is(err, ErrIncomplete) {
				t.Fatalf("invalid lifecycle accepted: %v", err)
			}
			rr := connectionRows(t, events)
			if rr[len(rr)-1].Complete {
				t.Fatalf("invalid lifecycle has complete final record: %+v", rr)
			}
		})
	}
}

func TestConnectionSequentialReuseAndConcurrentRequests(t *testing.T) {
	o, _, events := connectionObserver(t)
	var wg sync.WaitGroup
	for i := 0; i < 12; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			c := syntheticConnection(uint32(100 + i))
			for j := 0; j < 4; j++ {
				ctx, _, f := o.Begin(context.Background())
				q := o.TraceQueryStart(ctx, c, pgx.TraceQueryStartData{})
				o.TraceQueryEnd(q, c, pgx.TraceQueryEndData{})
				f()
			}
		}(i)
	}
	wg.Wait()
	if err := o.Close(); err != nil {
		t.Fatal(err)
	}
	rr := connectionRows(t, events)
	last := rr[len(rr)-1]
	if !last.Complete || last.RequestStarts != 48 || last.RequestFinishes != 48 || last.QueryStarts != 48 || last.QueryEnds != 48 || last.ActiveBindings != 0 {
		t.Fatalf("bad counts: %+v", last)
	}
}

func TestConnectionCancellationAndRawErrorPreserved(t *testing.T) {
	o, _, events := connectionObserver(t)
	parent, cancel := context.WithTimeout(context.Background(), time.Hour)
	defer cancel()
	ctx, _, f := o.Begin(parent)
	c := syntheticConnection(99)
	q := o.TraceQueryStart(ctx, c, pgx.TraceQueryStartData{SQL: "SECRET_SQL", Args: []any{unformattableSecret{}}})
	if a, _ := q.Deadline(); func() bool { b, _ := parent.Deadline(); return a != b }() {
		t.Fatal("deadline changed")
	}
	cancel()
	if q.Err() != context.Canceled {
		t.Fatal("cancellation changed")
	}
	o.TraceQueryEnd(q, c, pgx.TraceQueryEndData{Err: unformattableSecret{}, CommandTag: pgconn.NewCommandTag("TAG_SECRET")})
	f()
	if !errors.Is(o.Close(), ErrIncomplete) {
		t.Fatal("error accepted")
	}
	rr := connectionRows(t, events)
	if !rr[3].Error || rr[3].Complete || rr[3].QueryErrors != 1 || rr[3].BackendPID != 99 || strings.Contains(events.String(), "SECRET") {
		t.Fatalf("error binding lost: %+v", rr[3])
	}
}

func TestConnectionSinkFailureShortWriteAndLimit(t *testing.T) {
	for _, short := range []bool{false, true} {
		for at := 1; at <= 6; at++ {
			t.Run(strings.Join([]string{string(rune('0' + at)), map[bool]string{true: "short", false: "error"}[short]}, "/"), func(t *testing.T) {
				w := &failingWriter{failAt: at, short: short, err: errors.New("private sink unavailable")}
				o, err := NewWithConnectionEvents(io.Discard, w, testRun)
				want := w.err
				if short {
					want = io.ErrShortWrite
				}
				if at == 1 {
					if o != nil || !errors.Is(err, want) {
						t.Fatalf("constructor=%v", err)
					}
					return
				}
				if err != nil {
					t.Fatal(err)
				}
				ctx, _, f := o.Begin(context.Background())
				c := syntheticConnection(1)
				q := o.TraceQueryStart(ctx, c, pgx.TraceQueryStartData{})
				o.TraceQueryEnd(q, c, pgx.TraceQueryEndData{})
				f()
				if !errors.Is(o.Close(), want) || !errors.Is(o.Close(), want) || w.calls != at {
					t.Fatalf("sink error not sticky: calls=%d", w.calls)
				}
			})
		}
	}
	o, _, events := connectionObserver(t)
	o.connections.limit = int64(events.Len() + 1)
	_, _, f := o.Begin(context.Background())
	f()
	if !errors.Is(o.Close(), ErrLimit) || len(connectionRows(t, events)) != 1 {
		t.Fatal("connection cap allowed success or partial record")
	}
}

// Parent bounds stalls; no detached goroutine may drop/reorder diagnostics.
type gatedConnectionWriter struct {
	bytes.Buffer
	entered chan struct{}
	release chan struct{}
	calls   int
}

func (w *gatedConnectionWriter) Write(p []byte) (int, error) {
	w.calls++
	if w.calls == 3 {
		close(w.entered)
		<-w.release
	}
	return w.Buffer.Write(p)
}
func TestConnectionStalledWriterIsSynchronous(t *testing.T) {
	w := &gatedConnectionWriter{entered: make(chan struct{}), release: make(chan struct{})}
	o, err := NewWithConnectionEvents(io.Discard, w, testRun)
	if err != nil {
		t.Fatal(err)
	}
	ctx, _, f := o.Begin(context.Background())
	c := syntheticConnection(1)
	done := make(chan context.Context, 1)
	go func() { done <- o.TraceQueryStart(ctx, c, pgx.TraceQueryStartData{}) }()
	select {
	case <-w.entered:
	case <-time.After(time.Second):
		t.Fatal("callback did not enter writer")
	}
	select {
	case <-done:
		t.Fatal("callback silently passed stalled writer")
	default:
	}
	close(w.release)
	var q context.Context
	select {
	case q = <-done:
	case <-time.After(time.Second):
		t.Fatal("callback failed to resume")
	}
	o.TraceQueryEnd(q, c, pgx.TraceQueryEndData{})
	f()
	if err := o.Close(); err != nil {
		t.Fatal(err)
	}
}

func TestConnectionAfterCloseAndWriterOwnership(t *testing.T) {
	for _, event := range []string{"begin", "start", "end", "finish", "driver"} {
		t.Run(event, func(t *testing.T) {
			sw, cw := new(parentOwnedWriter), new(parentOwnedWriter)
			o, err := NewWithConnectionEvents(sw, cw, testRun)
			if err != nil {
				t.Fatal(err)
			}
			ctx, _, f := o.Begin(context.Background())
			f()
			if err := o.Close(); err != nil {
				t.Fatal(err)
			}
			before := cw.Len()
			if err := o.Close(); err != nil || cw.Len() != before {
				t.Fatal("Close not idempotent")
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
			case "driver":
				_, done := o.BeginDriverOperation(ctx, DriverOperationConnect)
				done(nil)
			}
			if !errors.Is(o.Close(), ErrClosed) || sw.closed || cw.closed {
				t.Fatal("post-close/ownership failure")
			}
			rr := connectionRows(t, &cw.Buffer)
			if rr[len(rr)-1].Type != "invalidated" || rr[len(rr)-1].Complete {
				t.Fatal("connection footer not invalidated")
			}
		})
	}
}

func TestConnectionConstructorAndSummaryFailure(t *testing.T) {
	for _, run := range []string{"", "SECRET_LABEL", "00000000-0000-0000-0000-000000000000"} {
		var a, b bytes.Buffer
		if o, err := NewWithConnectionEvents(&a, &b, run); o != nil || err == nil || a.Len() != 0 || b.Len() != 0 {
			t.Fatal("invalid run accepted or wrote output")
		}
	}
	var b bytes.Buffer
	if _, err := NewWithConnectionEvents(&b, nil, testRun); err == nil || b.Len() != 0 {
		t.Fatal("nil sink accepted")
	}
	if _, err := NewWithConnectionEvents(nil, &b, testRun); err == nil || b.Len() != 0 {
		t.Fatal("nil summary accepted")
	}
	w := &failingWriter{failAt: 2, err: errors.New("summary failed")}
	o, err := NewWithConnectionEvents(w, &b, testRun)
	if err != nil {
		t.Fatal(err)
	}
	_, _, f := o.Begin(context.Background())
	f()
	if !errors.Is(o.Close(), w.err) {
		t.Fatal("summary failure lost")
	}
	rr := connectionRows(t, &b)
	if rr[len(rr)-1].Complete {
		t.Fatal("summary loss allowed complete connection footer")
	}
}

func TestConnectionMismatchedEndCannotReleaseOrRepairBinding(t *testing.T) {
	for _, kind := range []string{"connection", "request", "nil_context", "nil_connection"} {
		t.Run(kind, func(t *testing.T) {
			o, _, events := connectionObserver(t)
			ctx, _, f := o.Begin(context.Background())
			c := syntheticConnection(81)
			q := o.TraceQueryStart(ctx, c, pgx.TraceQueryStartData{})
			wrongCtx, wrongConn := q, c
			var finishOther func()
			switch kind {
			case "connection":
				wrongConn = syntheticConnection(81)
			case "request":
				second, _, finish := o.Begin(context.Background())
				finishOther = finish
				wrongCtx = context.WithValue(q, requestKey{}, second.Value(requestKey{}))
			case "nil_context":
				wrongCtx = nil
			case "nil_connection":
				wrongConn = nil
			}
			o.TraceQueryEnd(wrongCtx, wrongConn, pgx.TraceQueryEndData{})
			rr := connectionRows(t, events)
			bad := rr[len(rr)-1]
			if bad.Type != "query_end" || bad.Complete || bad.BackendPID != 0 || bad.ActiveBindings != 1 || bad.ConnectionViolations != 1 {
				t.Fatalf("mismatch asserted or released binding: %+v", bad)
			}
			o.TraceQueryEnd(q, c, pgx.TraceQueryEndData{})
			rr = connectionRows(t, events)
			drained := rr[len(rr)-1]
			if drained.ActiveBindings != 0 || drained.Complete || drained.QueryID != q.Value(queryKey{}).(*query).binding.id {
				t.Fatalf("correction repaired acceptance or did not drain: %+v", drained)
			}
			f()
			if finishOther != nil {
				finishOther()
			}
			if !errors.Is(o.Close(), ErrIncomplete) {
				t.Fatal("correction erased failure")
			}
		})
	}
}

func TestConnectionNewHandleWithRecycledPIDAndUnknownExtraRequests(t *testing.T) {
	o, _, events := connectionObserver(t)
	// No completed-PID map: reincarnation is the independent parent's concern.
	for i := 0; i < 3; i++ {
		ctx, id, f := o.Begin(context.WithValue(context.Background(), "X-Query-Observation-ID", "CLIENT_SECRET"))
		if id != testRun+"-"+string(rune('1'+i)) {
			t.Fatal("caller selected ID")
		}
		if i < 2 {
			c := syntheticConnection(71)
			q := o.TraceQueryStart(ctx, c, pgx.TraceQueryStartData{})
			o.TraceQueryEnd(q, c, pgx.TraceQueryEndData{})
		}
		f()
	}
	if len(o.connections.byConn) != 0 || len(o.connections.byPID) != 0 {
		t.Fatal("completed connection retained")
	}
	if err := o.Close(); err != nil {
		t.Fatal(err)
	}
	rr := connectionRows(t, events)
	begins, finishes := 0, 0
	ids := make(map[string]bool)
	for _, r := range rr {
		if r.Type == "request_begin" {
			begins++
			ids[r.RequestID] = true
		}
		if r.Type == "request_finish" {
			finishes++
		}
	}
	if begins != 3 || finishes != 3 || len(ids) != 3 || strings.Contains(events.String(), "CLIENT_SECRET") {
		t.Fatal("query-free extra request missing or client data leaked")
	}
}

func TestConnectionConcurrentDuplicateCallbacksAndClose(t *testing.T) {
	o, _, events := connectionObserver(t)
	ctx, _, f := o.Begin(context.Background())
	c := syntheticConnection(55)
	q := o.TraceQueryStart(ctx, c, pgx.TraceQueryStartData{})
	var wg sync.WaitGroup
	for i := 0; i < 16; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); o.TraceQueryEnd(q, c, pgx.TraceQueryEndData{}); f(); _ = o.Close() }()
	}
	wg.Wait()
	if o.Close() == nil {
		t.Fatal("concurrent invalid lifecycle accepted")
	}
	rr := connectionRows(t, events)
	if rr[len(rr)-1].Complete {
		t.Fatal("concurrent close left valid final record")
	}
}

func TestConnectionFooterLimitAndSummaryFooterError(t *testing.T) {
	o, _, events := connectionObserver(t)
	_, _, f := o.Begin(context.Background())
	f()
	before := events.Len()
	o.connections.limit = int64(before + 1)
	if !errors.Is(o.Close(), ErrLimit) || events.Len() != before {
		t.Fatal("footer limit wrote partial/success record")
	}
	summary := &failingWriter{failAt: 3, err: errors.New("summary footer failed")}
	var conn bytes.Buffer
	o, err := NewWithConnectionEvents(summary, &conn, testRun)
	if err != nil {
		t.Fatal(err)
	}
	_, _, f = o.Begin(context.Background())
	f()
	if !errors.Is(o.Close(), summary.err) {
		t.Fatal("summary footer error lost")
	}
	rr := connectionRows(t, &conn)
	if rr[len(rr)-1].Complete {
		t.Fatal("summary footer failure accepted")
	}
}

func TestConnectionDriverFailureInvalidatesAcceptance(t *testing.T) {
	o, _, events := connectionObserver(t)
	ctx, _, f := o.Begin(context.Background())
	_, end := o.BeginDriverOperation(ctx, DriverOperationConnect)
	end(unformattableSecret{})
	f()
	if !errors.Is(o.Close(), ErrIncomplete) {
		t.Fatal("driver failure accepted")
	}
	rr := connectionRows(t, events)
	last := rr[len(rr)-1]
	if last.Complete || last.DriverErrors != 1 || strings.Contains(events.String(), "SECRET") {
		t.Fatal("driver error missing or leaked")
	}
}

func TestConnectionForeignIDsNeverCopied(t *testing.T) {
	var foreignSummary, foreignEvents bytes.Buffer
	const foreignRun = "11111111-2222-4333-8444-555555555555"
	foreign, err := NewWithConnectionEvents(&foreignSummary, &foreignEvents, foreignRun)
	if err != nil {
		t.Fatal(err)
	}
	ctx, _, finish := foreign.Begin(context.Background())
	c := syntheticConnection(61)
	q := foreign.TraceQueryStart(ctx, c, pgx.TraceQueryStartData{})
	local, _, events := connectionObserver(t)
	local.TraceQueryEnd(q, c, pgx.TraceQueryEndData{})
	lq := local.TraceQueryStart(ctx, c, pgx.TraceQueryStartData{})
	local.TraceQueryEnd(lq, c, pgx.TraceQueryEndData{})
	if !errors.Is(local.Close(), ErrIncomplete) || strings.Contains(events.String(), foreignRun) {
		t.Fatal("foreign identities copied or accepted")
	}
	foreign.TraceQueryEnd(q, c, pgx.TraceQueryEndData{})
	finish()
	if err := foreign.Close(); err != nil {
		t.Fatal("foreign observer changed by local callbacks", err)
	}
}

func TestConnectionPrematureFinishStaysIncompleteBeforeLateEnd(t *testing.T) {
	o, summary, events := connectionObserver(t)
	ctx, _, finish := o.Begin(context.Background())
	c := syntheticConnection(42)
	q := o.TraceQueryStart(ctx, c, pgx.TraceQueryStartData{})
	finish()
	_, _, secondFinish := o.Begin(context.Background())
	rr := connectionRows(t, events)
	if rr[len(rr)-1].Complete {
		t.Fatal("known premature finish was forgotten before late end")
	}
	o.TraceQueryEnd(q, c, pgx.TraceQueryEndData{})
	assertConnectionBindingDrained(t, o, q)
	rr = connectionRows(t, events)
	late := rr[len(rr)-1]
	if late.Type != "query_end" || late.Complete || late.BackendPID != 0 || late.ActiveBindings != 0 || late.ConnectionViolations != 2 || late.LateEnds != 1 {
		t.Fatalf("late cleanup changed rejection or PID attribution: %+v", late)
	}
	secondFinish()
	if !errors.Is(o.Close(), ErrIncomplete) {
		t.Fatal("premature finish accepted")
	}
	if rr = connectionRows(t, events); rr[len(rr)-1].Complete {
		t.Fatal("late cleanup produced a complete footer")
	}
	summaries := records(t, summary)
	if summaries[1].Complete || summaries[1].ActiveQueries != 1 || summaries[len(summaries)-1].Complete {
		t.Fatal("late cleanup repaired the premature summary or summary footer")
	}
}

func assertConnectionBindingDrained(t *testing.T, o *Observer, ctx context.Context) {
	t.Helper()
	b := &ctx.Value(queryKey{}).(*query).binding
	if o.activeQueries != 0 || len(o.connections.byConn) != 0 || len(o.connections.byPID) != 0 || !b.ended || b.conn != nil || b.pgConn != nil {
		t.Fatalf("completed query retained: active=%d connections=%d pids=%d ended=%v conn=%p pgConn=%p",
			o.activeQueries, len(o.connections.byConn), len(o.connections.byPID), b.ended, b.conn, b.pgConn)
	}
}

func TestConnectionRepeatedLateEndsReleaseBindingsAfterFailure(t *testing.T) {
	for _, outcome := range []string{"success", "query_error", "canceled", "deadline"} {
		for _, sink := range []string{"healthy", "connection_error", "summary_error", "connection_limit"} {
			t.Run(outcome+"/"+sink, func(t *testing.T) {
				var summary, events io.Writer = io.Discard, io.Discard
				sinkError := errors.New("private sink failure")
				if sink == "connection_error" {
					events = &failingWriter{failAt: 3, err: sinkError}
				}
				if sink == "summary_error" {
					summary = &failingWriter{failAt: 2, err: sinkError}
				}
				o, err := NewWithConnectionEvents(summary, events, testRun)
				if err != nil {
					t.Fatal(err)
				}
				if sink == "connection_limit" {
					o.connections.limit = o.connections.written + 1
				}
				const iterations = 64
				for i := 0; i < iterations; i++ {
					cause := errors.New("original private cancellation cause")
					parent, cancel := context.WithCancelCause(context.Background())
					var queryError error
					var deadlineCancel context.CancelFunc
					switch outcome {
					case "query_error":
						queryError = unformattableSecret{}
					case "canceled":
						queryError = context.Canceled
					case "deadline":
						parent, deadlineCancel = context.WithDeadlineCause(parent, time.Now().Add(-time.Second), cause)
						queryError = context.DeadlineExceeded
					}
					ctx, _, finish := o.Begin(parent)
					c := syntheticConnection(uint32(i + 1))
					q := o.TraceQueryStart(ctx, c, pgx.TraceQueryStartData{})
					if o.connections.byConn[c] != q.Value(queryKey{}) || o.connections.byPID[c.PgConn().PID()] != q.Value(queryKey{}) {
						t.Fatal("new connection was not bound after prior failure")
					}
					if outcome == "canceled" {
						cancel(cause)
					}
					finish()
					// Even after finish, a different connection with the same PID
					// cannot release the original binding or assert attribution.
					wrong := syntheticConnection(c.PgConn().PID())
					o.TraceQueryEnd(q, wrong, pgx.TraceQueryEndData{})
					if len(o.connections.byConn) != 1 || len(o.connections.byPID) != 1 {
						t.Fatal("mismatched late end released original binding")
					}
					o.TraceQueryEnd(q, c, pgx.TraceQueryEndData{Err: queryError})
					assertConnectionBindingDrained(t, o, q)
					if (outcome == "canceled" || outcome == "deadline") && (q.Err() != queryError || context.Cause(q) != cause) {
						t.Fatal("original cancellation/deadline or cause changed")
					}
					cancel(nil)
					if deadlineCancel != nil {
						deadlineCancel()
					}
				}
				if o.connections.violations != 3*iterations || o.counts.LateEnds != 2*iterations || o.counts.DuplicateEnds != iterations {
					t.Fatal("binding cleanup erased lifecycle failures")
				}
				wantErrors := uint64(0)
				if outcome != "success" {
					wantErrors = iterations
				}
				if o.counts.QueryErrors != wantErrors {
					t.Fatal("query errors lost")
				}
				closeErr := o.Close()
				if !errors.Is(closeErr, ErrIncomplete) {
					t.Fatal("late cleanup repaired acceptance")
				}
				if (sink == "connection_error" || sink == "summary_error") && !errors.Is(closeErr, sinkError) {
					t.Fatal("original sink error lost")
				}
				if sink == "connection_limit" && !errors.Is(closeErr, ErrLimit) {
					t.Fatal("output limit lost")
				}
			})
		}
	}
}
