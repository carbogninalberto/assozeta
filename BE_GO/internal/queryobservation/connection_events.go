package queryobservation

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"strconv"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

// NewWithConnectionEvents explicitly enables private connection attribution in
// addition to the unchanged v1 summary schema. Supply separate, parent-owned
// writers and a parent-generated run UUID. Neither writer is closed, even on
// constructor failure; a failure can leave a header in the other sink.
//
// Starts are written synchronously before the pgx callback returns. The caller
// must bound writer stalls externally and reject failed/timed-out finalization.
// No SQL is executed and callback observations do not prove server execution or
// transaction overlap. Only the real callback connection supplies a PID; there
// is intentionally no API accepting a PID or client-selected observation ID.
func NewWithConnectionEvents(summaryWriter, connectionWriter io.Writer, runID string) (*Observer, error) {
	if connectionWriter == nil {
		return nil, errors.New("query connection observation requires a writer")
	}
	o, err := New(summaryWriter, runID)
	if err != nil {
		return nil, err
	}
	o.connections = &connectionEvents{
		writer: connectionWriter, limit: maxBytes,
		byConn: make(map[*pgx.Conn]*query), byPID: make(map[uint32]*query),
	}
	o.writeConnectionLocked(o.connectionRecordLocked("header", nil, nil, 0, false, false))
	if o.connections.sinkErr != nil {
		return nil, o.connections.sinkErr
	}
	return o, nil
}

// Bind both object identities as well as the measured PID: PID equality alone
// cannot certify the original connection or its backend incarnation. All fields
// are private and protected by Observer.mu. Completed tokens retain no handles.
type connectionBinding struct {
	id     string
	conn   *pgx.Conn
	pgConn *pgconn.PgConn
	pid    uint32
	ended  bool
}

type connectionEvents struct {
	writer                        io.Writer
	limit, written                int64
	sinkErr                       error
	sequence, queries, violations uint64
	byConn                        map[*pgx.Conn]*query
	byPID                         map[uint32]*query
}

// This schema is separate from record and never changes New's v1 bytes. All
// counters are cumulative observer totals (including on request/query events).
type connectionRecord struct {
	Stream     string `json:"stream"`
	Version    int    `json:"version"`
	Type       string `json:"type"`
	RunID      string `json:"run_id"`
	Sequence   uint64 `json:"sequence"`
	RequestID  string `json:"request_id,omitempty"`
	QueryID    string `json:"query_id,omitempty"`
	BackendPID uint32 `json:"backend_pid,omitempty"`
	Error      bool   `json:"error"`
	Complete   bool   `json:"complete"`
	counters
	RequestStarts          uint64 `json:"request_starts"`
	RequestFinishes        uint64 `json:"request_finishes"`
	FinishCalls            uint64 `json:"finish_calls"`
	ActiveRequests         uint64 `json:"active_requests"`
	ActiveQueries          uint64 `json:"active_queries"`
	ActiveBindings         uint64 `json:"active_bindings"`
	ConnectionViolations   uint64 `json:"connection_violations"`
	ActiveDriverOperations uint64 `json:"active_driver_operations"`
	PostCloseEvents        uint64 `json:"post_close_events"`
}

func (o *Observer) connectionViolationLocked() { o.connections.violations++ }

func (o *Observer) connectionsCleanLocked() bool {
	e := o.connections
	return e == nil || e.sinkErr == nil && e.violations == 0 && len(e.byConn) == 0 && len(e.byPID) == 0
}

func (o *Observer) connectionStartLocked(ctx context.Context, conn *pgx.Conn, q *query) {
	e := o.connections
	if e == nil || o.closed {
		return
	}
	e.queries++
	q.binding.id = o.runID + "-q" + strconv.FormatUint(e.queries, 10)
	old, _ := ctx.Value(queryKey{}).(*query)
	valid := q.request != nil && !q.request.finished && old == nil && conn != nil
	var p *pgconn.PgConn
	var pid uint32
	if conn != nil {
		p = conn.PgConn()
		if p != nil {
			pid = p.PID()
		}
	}
	valid = valid && p != nil && pid > 0 && e.byConn[conn] == nil && e.byPID[pid] == nil
	if valid {
		q.binding.conn, q.binding.pgConn, q.binding.pid = conn, p, pid
		e.byConn[conn], e.byPID[pid] = q, q
	} else {
		o.connectionViolationLocked()
		pid = 0 // An invalid start must never assert a PID attribution.
	}
	o.connectionEventLocked("query_start", q.request, q, pid, false, valid)
}

func (o *Observer) connectionEndLocked(ctx context.Context, conn *pgx.Conn, queryError bool) {
	e := o.connections
	if e == nil || o.closed {
		return
	}
	q, _ := ctx.Value(queryKey{}).(*query)
	current, _ := ctx.Value(requestKey{}).(*request)
	var r *request
	var pid uint32
	valid := false
	if q != nil && q.owner == o {
		r = q.request
		b := &q.binding
		// Match ownership independently of request lifecycle validity. An exact
		// late end must release handles even after a premature finish. Mismatched
		// callbacks retain the binding; cleanup never erases sticky violations.
		matched := r != nil && current == r && !b.ended && b.conn != nil && conn == b.conn &&
			conn.PgConn() == b.pgConn && b.pgConn.PID() == b.pid && b.pid > 0 &&
			e.byConn[conn] == q && e.byPID[b.pid] == q
		if matched {
			valid = !r.finished
			if valid {
				pid = b.pid
			}
			delete(e.byConn, conn)
			delete(e.byPID, b.pid)
			b.ended = true
			b.conn = nil
			b.pgConn = nil
		}
	} else {
		// Do not serialize a foreign observer's query or request identifiers.
		q = nil
		if current != nil && current.owner == o {
			r = current
		}
	}
	if !valid {
		o.connectionViolationLocked()
	}
	o.connectionEventLocked("query_end", r, q, pid, queryError, valid)
}

func (o *Observer) connectionEventLocked(kind string, r *request, q *query, pid uint32, queryError, valid bool) {
	if o.connections == nil || o.closed {
		return
	}
	// A premature request summary cannot become acceptable again while waiting
	// for its late callbacks, even before v1 accounts those late callbacks.
	if kind == "request_finish" && !valid {
		o.connectionViolationLocked()
	}
	// Existing lifecycle/driver violations and errors are also acceptance failures.
	valid = valid && o.counts.Violations == 0 && o.counts.QueryErrors == 0 && o.counts.DriverErrors == 0 &&
		o.connections.violations == 0 && o.sinkErr == nil && o.connections.sinkErr == nil
	o.writeConnectionLocked(o.connectionRecordLocked(kind, r, q, pid, queryError, valid))
}

func (o *Observer) connectionRecordLocked(kind string, r *request, q *query, pid uint32, queryError, complete bool) connectionRecord {
	e := o.connections
	e.sequence++
	v := connectionRecord{
		Stream: "query_connection", Version: 1, Type: kind, RunID: o.runID, Sequence: e.sequence,
		BackendPID: pid, Error: queryError, Complete: complete, counters: o.counts,
		RequestStarts: o.sequence, RequestFinishes: o.requestFinishes, FinishCalls: o.finishCalls,
		ActiveRequests: o.sequence - o.requestFinishes, ActiveQueries: o.activeQueries,
		ActiveBindings: uint64(len(e.byConn)), ConnectionViolations: e.violations,
		ActiveDriverOperations: o.activeDriverOperations, PostCloseEvents: o.postCloseEvents,
	}
	if r != nil {
		v.RequestID = r.id
	}
	if q != nil {
		v.QueryID = q.binding.id
	}
	return v
}

func (o *Observer) writeConnectionLocked(v connectionRecord) {
	e := o.connections
	if e.sinkErr != nil {
		return
	}
	p, err := json.Marshal(v)
	if err != nil {
		e.sinkErr = err
		return
	}
	p = append(p, '\n')
	if int64(len(p)) > e.limit-e.written {
		e.sinkErr = ErrLimit
		return
	}
	n, err := e.writer.Write(p)
	if n >= 0 && n <= len(p) {
		e.written += int64(n)
	}
	if err != nil {
		e.sinkErr = err
	} else if n != len(p) {
		e.sinkErr = io.ErrShortWrite
	}
}
