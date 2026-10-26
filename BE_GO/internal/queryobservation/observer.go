// Package queryobservation provides opt-in, bounded pgx QueryTracer and driver
// lifecycle diagnostics. These callbacks do not confirm PostgreSQL wire traffic;
// batches, CopyFrom, and other direct PgConn work remain outside this interface.
package queryobservation

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"strconv"
	"sync"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

const maxBytes int64 = 64 << 20

var (
	ErrLimit      = errors.New("query observation output limit reached")
	ErrIncomplete = errors.New("query observation is incomplete or contains errors or violations")
	ErrClosed     = errors.New("query observation event after close")
)

type requestKey struct{}
type queryKey struct{}

type counters struct {
	driverCounters
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
}

type record struct {
	Type      string `json:"type"`
	Version   int    `json:"version"`
	RunID     string `json:"run_id"`
	RequestID string `json:"request_id,omitempty"`
	Sequence  uint64 `json:"sequence,omitempty"`
	Complete  bool   `json:"complete"`
	counters
	RequestStarts          uint64 `json:"request_starts"`
	RequestFinishes        uint64 `json:"request_finishes"`
	FinishCalls            uint64 `json:"finish_calls"`
	Summaries              uint64 `json:"summaries"`
	ActiveRequests         uint64 `json:"active_requests"`
	ActiveRequestsAtStart  uint64 `json:"active_requests_at_start"`
	ActiveQueries          uint64 `json:"active_queries"`
	ActiveDriverOperations uint64 `json:"active_driver_operations"`
	PostCloseEvents        uint64 `json:"post_close_events"`
}

type request struct {
	owner         *Observer
	id            string
	sequence      uint64
	counts        counters
	active        uint64
	activeDriver  uint64
	activeAtStart uint64
	finished      bool
}

type query struct {
	owner   *Observer
	request *request
	ended   bool
	binding connectionBinding
}

// Observer serializes accounting and JSONL writes. Its memory consists of
// counters, context-owned tokens, and optional active connection indexes; it
// retains no completed-ID map. Methods may run concurrently. A writer must not
// call back into Observer. Construct with New or NewWithConnectionEvents.
type Observer struct {
	mu                     sync.Mutex
	writer                 io.Writer
	runID                  string
	counts                 counters
	sequence               uint64
	requestFinishes        uint64
	finishCalls            uint64
	summaries              uint64
	activeQueries          uint64
	activeDriverOperations uint64
	postCloseEvents        uint64
	limit                  int64
	written                int64
	sinkErr                error
	closeErr               error
	closed                 bool
	invalidated            bool
	connections            *connectionEvents
}

var _ pgx.QueryTracer = (*Observer)(nil)

// New validates and canonicalizes the parent-generated run UUID, and writes a
// version-1 header. It never closes w, including on failure. Only generated IDs,
// counters and fixed schema fields enter the stream; SQL, arguments, command
// tags, raw errors, and arbitrary context/client text are never serialized.
func New(w io.Writer, runID string) (*Observer, error) {
	if w == nil {
		return nil, errors.New("query observation requires a writer")
	}
	id, err := uuid.Parse(runID)
	if err != nil || id == uuid.Nil {
		return nil, errors.New("query observation requires a nonzero run UUID")
	}
	o := &Observer{writer: w, runID: id.String(), limit: maxBytes}
	o.writeLocked(o.recordLocked("header"))
	if o.sinkErr != nil {
		return nil, o.sinkErr
	}
	return o, nil
}

// Begin derives a context preserving its parent's deadline, cancellation and
// values. The ID is the canonical run UUID plus a monotonic decimal sequence.
// Call finish once after all request-owned database work. Repeated finishes and
// reusing an observation context to Begin another request invalidate diagnostics.
func (o *Observer) Begin(ctx context.Context) (context.Context, string, func()) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.connections != nil && ctx == nil {
		ctx = context.Background()
		o.connectionViolationLocked()
	}
	o.sequence++
	r := &request{owner: o, sequence: o.sequence, id: o.runID + "-" + strconv.FormatUint(o.sequence, 10),
		activeAtStart: o.sequence - o.requestFinishes}
	if o.closed {
		o.afterCloseLocked()
	} else if previous, ok := ctx.Value(requestKey{}).(*request); ok {
		if previous.owner == o {
			o.violationLocked(previous, func(c *counters) { c.ReusedContexts++ })
			// The new summary must also show that its root was reused.
			r.counts.ReusedContexts++
			r.counts.Violations++
		} else {
			o.violationLocked(r, func(c *counters) { c.ForeignContexts++ })
		}
	}
	o.connectionEventLocked("request_begin", r, nil, 0, false, !o.closed)
	return context.WithValue(ctx, requestKey{}, r), r.id, func() { o.finish(r) }
}

// TraceQueryStart ignores data. Connection attribution is enabled only by
// NewWithConnectionEvents; New continues to accept nil/synthetic connections.
// Each callback counts, including missing, foreign, finished or reused contexts.
func (o *Observer) TraceQueryStart(ctx context.Context, conn *pgx.Conn, _ pgx.TraceQueryStartData) context.Context {
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.connections != nil && ctx == nil {
		ctx = context.Background()
	}
	o.counts.QueryStarts++
	q := &query{owner: o}
	defer func() { o.connectionStartLocked(ctx, conn, q) }()
	if o.closed {
		o.afterCloseLocked()
		return context.WithValue(ctx, queryKey{}, q)
	}
	r, _ := ctx.Value(requestKey{}).(*request)
	if r == nil {
		o.counts.UnboundStarts++
		o.violationLocked(nil, func(c *counters) { c.MissingContexts++ })
	} else if r.owner != o {
		o.counts.UnboundStarts++
		o.violationLocked(nil, func(c *counters) { c.ForeignContexts++ })
	} else {
		q.request = r
		r.counts.QueryStarts++
		r.active++
		if r.finished {
			o.violationLocked(r, func(c *counters) { c.LateStarts++ })
		}
	}
	if old, ok := ctx.Value(queryKey{}).(*query); ok {
		if old.owner == o {
			o.violationLocked(q.request, func(c *counters) { c.ReusedContexts++ })
		} else {
			o.violationLocked(q.request, func(c *counters) { c.ForeignContexts++ })
		}
	}
	o.activeQueries++
	return context.WithValue(ctx, queryKey{}, q)
}

// TraceQueryEnd counts every callback and error, including unknown/duplicate
// ends. Only the original query token can release its active-query count.
func (o *Observer) TraceQueryEnd(ctx context.Context, conn *pgx.Conn, data pgx.TraceQueryEndData) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.connections != nil && ctx == nil {
		ctx = context.Background()
	}
	defer func() { o.connectionEndLocked(ctx, conn, data.Err != nil) }()
	o.counts.QueryEnds++
	if data.Err != nil {
		o.counts.QueryErrors++
	}
	if o.closed {
		o.afterCloseLocked()
		return
	}
	q, _ := ctx.Value(queryKey{}).(*query)
	current, _ := ctx.Value(requestKey{}).(*request)
	if q == nil || q.owner != o {
		o.counts.UnboundEnds++
		var r *request
		if current != nil && current.owner == o {
			r = current
			r.counts.QueryEnds++
			r.counts.UnboundEnds++
			if data.Err != nil {
				r.counts.QueryErrors++
			}
			if r.finished {
				o.violationLocked(r, func(c *counters) { c.LateEnds++ })
			}
		}
		if q == nil {
			o.violationLocked(r, func(c *counters) { c.MissingContexts++ })
		} else {
			o.violationLocked(r, func(c *counters) { c.ForeignContexts++ })
		}
		return
	}
	r := q.request
	if r == nil {
		o.counts.UnboundEnds++
		// The matching unbound start already records the missing/foreign context.
	} else {
		r.counts.QueryEnds++
		if data.Err != nil {
			r.counts.QueryErrors++
		}
		if current != r {
			o.counts.UnboundEnds++
			r.counts.UnboundEnds++
			o.violationLocked(r, func(c *counters) { c.ContextMismatches++ })
		}
		if r.finished {
			o.violationLocked(r, func(c *counters) { c.LateEnds++ })
		}
	}
	if q.ended {
		o.violationLocked(r, func(c *counters) { c.DuplicateEnds++ })
		return
	}
	q.ended = true
	o.activeQueries--
	if r != nil {
		r.active--
	}
}

func (o *Observer) finish(r *request) {
	o.mu.Lock()
	defer o.mu.Unlock()
	valid := !r.finished && r.active == 0 && r.activeDriver == 0 && clean(r.counts)
	defer func() { o.connectionEventLocked("request_finish", r, nil, 0, false, valid) }()
	o.finishCalls++
	if o.closed {
		o.afterCloseLocked()
		return
	}
	if r.finished {
		o.violationLocked(r, func(c *counters) { c.DuplicateFinishes++ })
		return
	}
	r.finished = true
	o.requestFinishes++
	v := record{Type: "request", Version: 1, RunID: o.runID, RequestID: r.id, Sequence: r.sequence,
		counters: r.counts, RequestStarts: 1, RequestFinishes: 1, FinishCalls: 1, Summaries: 1, ActiveQueries: r.active,
		ActiveDriverOperations: r.activeDriver,
		ActiveRequestsAtStart:  r.activeAtStart,
		Complete:               r.active == 0 && r.activeDriver == 0 && clean(r.counts)}
	if o.writeLocked(v) {
		o.summaries++
	}
}

func clean(c counters) bool {
	return c.QueryStarts == c.QueryEnds && c.QueryErrors == 0 && c.Violations == 0 && c.UnboundStarts == 0 && c.UnboundEnds == 0 && c.driverCounters.clean()
}

func (o *Observer) violationLocked(r *request, add func(*counters)) {
	o.counts.Violations++
	add(&o.counts)
	if r != nil {
		r.counts.Violations++
		add(&r.counts)
	}
}

func (o *Observer) recordLocked(kind string) record {
	return record{Type: kind, Version: 1, RunID: o.runID, counters: o.counts,
		RequestStarts: o.sequence, RequestFinishes: o.requestFinishes, FinishCalls: o.finishCalls,
		Summaries: o.summaries, ActiveRequests: o.sequence - o.requestFinishes, ActiveQueries: o.activeQueries,
		ActiveDriverOperations: o.activeDriverOperations,
		PostCloseEvents:        o.postCloseEvents}
}

// Close writes the final footer once and returns sticky sink/lifecycle errors.
// It does not wait for active work, repair premature finishes, retry writes, or
// close the writer. Drain HTTP and DB work first; close the writer afterward.
// Acceptance requires all expected summaries and the LAST JSONL record to be a
// complete footer, and successful observer/writer finalization. A callback after
// Close appends one invalidation record (when the sink is healthy), and subsequent
// Close calls also return ErrClosed.
func (o *Observer) Close() error {
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.closed {
		return o.closeErr
	}
	o.closed = true
	f := o.recordLocked("footer")
	f.Complete = clean(o.counts) && f.ActiveRequests == 0 && f.ActiveQueries == 0 && f.ActiveDriverOperations == 0 && o.summaries == o.sequence && o.finishCalls == o.requestFinishes && o.sinkErr == nil && o.connectionsCleanLocked()
	if !f.Complete {
		o.closeErr = ErrIncomplete
	}
	o.writeLocked(f)
	o.closeErr = errors.Join(o.closeErr, o.sinkErr)
	if o.connections != nil {
		o.writeConnectionLocked(o.connectionRecordLocked("footer", nil, nil, 0, false, f.Complete && o.sinkErr == nil))
		o.closeErr = errors.Join(o.closeErr, o.connections.sinkErr)
		// Closed observers retain no active-connection index. Context tokens
		// still reject late callbacks; they cannot repair finalization.
		clear(o.connections.byConn)
		clear(o.connections.byPID)
	}
	return o.closeErr
}

func (o *Observer) afterCloseLocked() {
	o.postCloseEvents++
	o.counts.Violations++
	if o.invalidated {
		return
	}
	o.invalidated = true
	o.writeLocked(o.recordLocked("invalidated"))
	o.closeErr = errors.Join(o.closeErr, ErrClosed, o.sinkErr)
	if o.connections != nil {
		o.writeConnectionLocked(o.connectionRecordLocked("invalidated", nil, nil, 0, false, false))
		o.closeErr = errors.Join(o.closeErr, o.connections.sinkErr)
	}
}

func (o *Observer) writeLocked(v record) bool {
	if o.sinkErr != nil {
		return false
	}
	p, err := json.Marshal(v)
	if err != nil {
		o.sinkErr = err
		return false
	}
	p = append(p, '\n')
	if int64(len(p)) > o.limit-o.written {
		o.sinkErr = ErrLimit
		return false
	}
	n, err := o.writer.Write(p)
	if n >= 0 && n <= len(p) {
		o.written += int64(n)
	}
	if err != nil {
		o.sinkErr = err
		return false
	}
	if n != len(p) {
		o.sinkErr = io.ErrShortWrite
		return false
	}
	return true
}
