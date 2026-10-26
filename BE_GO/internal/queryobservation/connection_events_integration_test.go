//go:build queryobservationintegration

package queryobservation

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"os"
	"sync"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

// Unlike the older integration fixtures' post-drain bytes.Buffer, this sink is
// safe to inspect while callbacks write. It never blocks on a reader or grows
// past its fixed bound. Snapshots own their bytes; no mutable slice escapes.
type liveConnectionSink struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (s *liveConnectionSink) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(p) > (256<<10)-s.b.Len() {
		return 0, ErrLimit
	}
	return s.b.Write(p)
}

func (s *liveConnectionSink) snapshot() []byte {
	s.mu.Lock()
	defer s.mu.Unlock()
	return bytes.Clone(s.b.Bytes())
}

func liveDecode[T any](p []byte) ([]T, error) {
	d := json.NewDecoder(bytes.NewReader(p))
	d.DisallowUnknownFields()
	var rows []T
	for {
		var row T
		if err := d.Decode(&row); err == io.EOF {
			return rows, nil
		} else if err != nil {
			return nil, err
		}
		rows = append(rows, row)
	}
}

// A transparent tracer records the original callback error without replacing
// it. Its start receipt is taken immediately after the observer returns, before
// pgx can send the query: eventual visibility alone would miss an async writer.
type liveConnectionTracer struct {
	o      *Observer
	sink   *liveConnectionSink
	starts chan bool
	ends   chan error
}

func (r *liveConnectionTracer) TraceQueryStart(ctx context.Context, conn *pgx.Conn, data pgx.TraceQueryStartData) context.Context {
	ctx = r.o.TraceQueryStart(ctx, conn, data)
	rr, err := liveDecode[connectionRecord](r.sink.snapshot())
	visible := err == nil && len(rr) > 0 && rr[len(rr)-1].Type == "query_start"
	select {
	case r.starts <- visible:
	default: // Extra callbacks cannot stall; exact final counts reject them.
	}
	return ctx
}

func (r *liveConnectionTracer) TraceQueryEnd(ctx context.Context, conn *pgx.Conn, data pgx.TraceQueryEndData) {
	r.o.TraceQueryEnd(ctx, conn, data)
	select {
	case r.ends <- data.Err:
	default:
	}
}

type liveConnectionObservation struct {
	o               *Observer
	db              *sql.DB
	conn            *sql.Conn
	summary, events *liveConnectionSink
	trace           *liveConnectionTracer
	run, requestID  string
	finish          func()
	pid             uint32
	cleanup         <-chan struct{}
	private         []string
}

func liveObserveConnection(t *testing.T, cfg *pgx.ConnConfig, ctx context.Context) (*liveConnectionObservation, context.Context) {
	t.Helper()
	f := &liveConnectionObservation{summary: new(liveConnectionSink), events: new(liveConnectionSink), run: uuid.NewString()}
	var err error
	f.o, err = NewWithConnectionEvents(f.summary, f.events, f.run)
	if err != nil {
		t.Fatal("cannot initialize connection observation")
	}
	request, id, finish := f.o.Begin(context.WithValue(ctx, pgPrivacyKey{}, pgPrivacy))
	f.requestID, f.finish = id, finish
	f.trace = &liveConnectionTracer{o: f.o, sink: f.events, starts: make(chan bool, 1), ends: make(chan error, 1)}
	observed := cfg.Copy()
	observed.Tracer = f.trace
	f.db = database.OpenObservedDB(*observed, f.o)
	f.db.SetMaxOpenConns(1)
	f.db.SetMaxIdleConns(1)
	f.private = []string{pgPrivacy, os.Getenv("ASSOZETA_QUERY_OBSERVATION_DSN"), cfg.Host, cfg.Password, "SELECT", "pg_advisory", "pg_backend_pid"}
	t.Cleanup(func() {
		if f.conn != nil {
			_ = f.conn.Close()
		}
		_ = f.db.Close()
		if f.cleanup != nil {
			join, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			select {
			case <-f.cleanup:
			case <-join.Done():
				t.Error("owned driver backend cleanup did not join")
			}
		}
		_ = f.o.Close()
	})
	f.conn, err = f.db.Conn(request)
	if err != nil {
		t.Fatal("cannot acquire owned observed connection (details suppressed)")
	}
	// Inspect a real owned driver only while Raw holds it. Retain no driver or
	// pgx handle; the cleanup channel joins pgx's asynchronous socket teardown.
	err = f.conn.Raw(func(raw any) error {
		native, ok := raw.(interface{ Conn() *pgx.Conn })
		if !ok || !database.SupportsOwnedTransactions(raw) || native.Conn() == nil {
			return errors.New("unexpected owned driver")
		}
		f.pid = native.Conn().PgConn().PID()
		f.cleanup = native.Conn().PgConn().CleanupDone()
		return nil
	})
	if err != nil || f.pid == 0 || f.cleanup == nil {
		t.Fatal("owned driver identity or cleanup contract failed")
	}
	return f, request
}

func liveUnobservedConnection(t *testing.T, cfg *pgx.ConnConfig, ctx context.Context) *pgx.Conn {
	t.Helper()
	plain := cfg.Copy()
	plain.Tracer = nil
	c, err := pgx.ConnectConfig(ctx, plain)
	if err != nil {
		t.Fatal("cannot connect independent fixture session (details suppressed)")
	}
	t.Cleanup(func() {
		closeCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if c.Close(closeCtx) != nil {
			t.Error("independent fixture session close failed (details suppressed)")
		}
		select {
		case <-c.PgConn().CleanupDone():
		case <-closeCtx.Done():
			t.Error("independent fixture session cleanup did not join")
		}
	})
	return c
}

// A successful try-lock establishes ownership; no test waits to acquire a
// possibly foreign key. UUID-derived positive int32 keys use the two-key advisory
// namespace (objsubid=2). Only our controller ever unlocks this session lock.
func liveAdvisoryBlocker(t *testing.T, ctx context.Context, controller *pgx.Conn) (int32, int32, func()) {
	t.Helper()
	id := uuid.New()
	a := int32(binary.BigEndian.Uint32(id[:4])&0x7fffffff) | 1
	b := int32(binary.BigEndian.Uint32(id[4:8])&0x7fffffff) | 1
	var locked bool
	if controller.QueryRow(ctx, `SELECT pg_try_advisory_lock($1::int, $2::int)`, a, b).Scan(&locked) != nil || !locked {
		t.Fatal("cannot own unique advisory blocker (details suppressed)")
	}
	var once sync.Once
	release := func() {
		once.Do(func() {
			releaseCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			var released bool
			if controller.QueryRow(releaseCtx, `SELECT pg_advisory_unlock($1::int, $2::int)`, a, b).Scan(&released) != nil || !released {
				t.Error("owned advisory blocker release failed (details suppressed)")
				// Closing only our controller is the fallback release, never
				// cancellation/termination of another backend.
				closeCtx, stop := context.WithTimeout(context.Background(), 5*time.Second)
				defer stop()
				_ = controller.Close(closeCtx)
			}
		})
	}
	t.Cleanup(release)
	return a, b, release
}

const liveBlockedSQL = `SELECT pg_advisory_xact_lock($1::int, $2::int), pg_backend_pid(), g FROM generate_series(1, 2) AS g`

// Polls collect positive catalog evidence, rather than treating elapsed time as
// proof of blocking. The monitor is an independently owned, unobserved session.
func liveAwaitAdvisoryWait(t *testing.T, ctx context.Context, monitor, controller *pgx.Conn, f *liveConnectionObservation, start connectionRecord, a, b int32) time.Time {
	t.Helper()
	if start.BackendPID != f.pid || f.pid == controller.PgConn().PID() || f.pid == monitor.PgConn().PID() || controller.PgConn().PID() == monitor.PgConn().PID() {
		t.Fatal("event PID does not identify a distinct actual workload backend")
	}
	tick := time.NewTicker(10 * time.Millisecond)
	defer tick.Stop()
	for {
		var backendStart time.Time
		err := monitor.QueryRow(ctx, `SELECT a.backend_start
			FROM pg_catalog.pg_stat_activity a
			JOIN pg_catalog.pg_locks w ON w.pid = a.pid
			JOIN pg_catalog.pg_locks h ON h.locktype = w.locktype AND h.database = w.database
			 AND h.classid = w.classid AND h.objid = w.objid AND h.objsubid = w.objsubid
			JOIN pg_catalog.pg_stat_activity owner ON owner.pid = h.pid
			WHERE a.pid = $1::int AND a.datname = current_database() AND a.usename = current_user
			 AND a.state = 'active' AND a.wait_event_type = 'Lock' AND a.wait_event = 'advisory'
			 AND a.query = $5::text AND a.backend_start IS NOT NULL
			 AND w.locktype = 'advisory' AND NOT w.granted AND w.mode = 'ExclusiveLock'
			 AND w.classid = $3::oid AND w.objid = $4::oid AND w.objsubid = 2
			 AND w.database = (SELECT oid FROM pg_catalog.pg_database WHERE datname = current_database())
			 AND h.granted AND h.mode = 'ExclusiveLock' AND h.pid = $2::int
			 AND owner.datname = current_database() AND owner.usename = current_user
			 AND $2::int = ANY(pg_catalog.pg_blocking_pids(a.pid))`,
			start.BackendPID, controller.PgConn().PID(), a, b, liveBlockedSQL).Scan(&backendStart)
		if err == nil && !backendStart.IsZero() {
			return backendStart
		}
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			t.Fatal("independent lock catalog sample failed (details suppressed)")
		}
		select {
		case <-ctx.Done():
			t.Fatal("catalog never confirmed the exact owned advisory wait")
		case <-tick.C:
		}
	}
}

func (f *liveConnectionObservation) beforeEnd(t *testing.T) connectionRecord {
	t.Helper()
	rr, err := liveDecode[connectionRecord](f.events.snapshot())
	if err != nil || len(rr) != 3 || rr[0].Type != "header" || rr[1].Type != "request_begin" || rr[2].Type != "query_start" {
		t.Fatal("start not visible before query return, or an end arrived before rows close")
	}
	start := rr[2]
	if start.RequestID != f.requestID || f.requestID != f.run+"-1" || start.QueryID != f.run+"-q1" || start.BackendPID != f.pid || !start.Complete || start.Error || start.QueryStarts != 1 || start.QueryEnds != 0 || start.ActiveQueries != 1 || start.ActiveBindings != 1 || start.ActiveDriverOperations != 0 {
		t.Fatal("live start identity, binding, or driver accounting failed")
	}
	summaries, err := liveDecode[record](f.summary.snapshot())
	if err != nil || len(summaries) != 1 || summaries[0].Type != "header" {
		t.Fatal("request finished before its query and rows drained")
	}
	return start
}

type liveQueryResult struct {
	rows *sql.Rows
	err  error
}

// At most one worker, one buffered outcome, and one bounded join per case. The
// cleanup releases our blocker and cancels our query even if a barrier fails.
func liveStartQuery(t *testing.T, ctx context.Context, f *liveConnectionObservation, release func(), query string, args ...any) (func() liveQueryResult, context.CancelFunc, func()) {
	t.Helper()
	queryCtx, cancel := context.WithCancel(ctx)
	result, done := make(chan liveQueryResult, 1), make(chan struct{})
	go func() {
		defer close(done)
		rows, err := f.conn.QueryContext(queryCtx, query, args...)
		result <- liveQueryResult{rows, err}
	}()
	var taken bool
	var outcome liveQueryResult
	t.Cleanup(func() {
		cancel()
		release()
		join, stop := context.WithTimeout(context.Background(), 5*time.Second)
		defer stop()
		select {
		case <-done:
			if !taken {
				outcome = <-result
			}
			if outcome.rows != nil {
				_ = outcome.rows.Close()
			}
		case <-join.Done():
			t.Error("owned query worker did not join")
		}
	})
	return func() liveQueryResult {
			t.Helper()
			outcome = pgAwait(t, ctx, result)
			taken = true
			pgAwait(t, ctx, done)
			return outcome
		}, cancel, func() {
			t.Helper()
			select {
			case <-done:
				t.Fatal("blocked SQL returned before the owned advisory blocker was released")
			default:
			}
		}
}

func liveAwaitLocksReleased(t *testing.T, ctx context.Context, monitor *pgx.Conn, a, b int32, ownerPID, workloadPID uint32) {
	t.Helper()
	tick := time.NewTicker(10 * time.Millisecond)
	defer tick.Stop()
	for {
		var released bool
		if monitor.QueryRow(ctx, `SELECT NOT EXISTS (SELECT 1 FROM pg_catalog.pg_locks WHERE locktype = 'advisory' AND classid = $1::oid AND objid = $2::oid AND objsubid = 2 AND pid IN ($3::int, $4::int))`, a, b, ownerPID, workloadPID).Scan(&released) != nil {
			t.Fatal("owned advisory lock cleanup sample failed (details suppressed)")
		}
		if released {
			return
		}
		select {
		case <-ctx.Done():
			t.Fatal("owned advisory locks survived query and controller cleanup")
		case <-tick.C:
		}
	}
}

func (f *liveConnectionObservation) finalize(t *testing.T, ctx context.Context, start connectionRecord, queryErr error) {
	t.Helper()
	callbackErr := pgAwait(t, ctx, f.trace.ends)
	if callbackErr != queryErr {
		t.Fatal("observer/driver did not preserve the original query error identity")
	}
	f.finish()
	if f.conn.Close() != nil || f.db.Stats().InUse != 0 || f.db.Close() != nil {
		t.Fatal("owned driver connection or rows did not drain (details suppressed)")
	}
	pgAwait(t, ctx, f.cleanup)
	// Close clears indexes even on failure. Check both beforehand so rejected
	// error runs cannot hide an orphaned PID binding behind that final cleanup.
	f.o.mu.Lock()
	drained := f.o.activeQueries == 0 && f.o.activeDriverOperations == 0 &&
		len(f.o.connections.byConn) == 0 && len(f.o.connections.byPID) == 0
	f.o.mu.Unlock()
	if !drained {
		t.Fatal("query, driver, or connection/PID bindings did not drain before finalization")
	}
	closeErr := f.o.Close()
	failed := queryErr != nil
	if failed {
		f.private = append(f.private, queryErr.Error())
	}
	if !failed && closeErr != nil || failed && !errors.Is(closeErr, ErrIncomplete) {
		t.Fatal("observer finalization did not reject errors or accept clean drain")
	}
	var queryErrors, connectionViolations uint64
	if failed {
		queryErrors, connectionViolations = 1, 1 // An errored request_finish is also rejected.
	}
	want := counters{QueryStarts: 1, QueryEnds: 1, QueryErrors: queryErrors,
		driverCounters: driverCounters{DriverStarts: 1, DriverEnds: 1, ConnectStarts: 1, ConnectEnds: 1}}
	rr, err := liveDecode[connectionRecord](f.events.snapshot())
	if err != nil || len(rr) != 6 {
		t.Fatal("connection stream is not the exact final six-record schema")
	}
	for i, row := range rr {
		if row.Stream != "query_connection" || row.Version != 1 || row.RunID != f.run || row.Sequence != uint64(i+1) {
			t.Fatal("connection stream identity or gapless sequence failed")
		}
	}
	if rr[0].Type != "header" || rr[0].Complete || rr[0].counters != (counters{}) || rr[1].Type != "request_begin" || rr[1].RequestID != f.requestID ||
		start != rr[2] || start.Type != "query_start" || start.RequestID != f.requestID || f.requestID != f.run+"-1" || start.QueryID != f.run+"-q1" || start.BackendPID != f.pid || !start.Complete || start.Error || start.QueryStarts != 1 || start.QueryEnds != 0 || start.QueryErrors != 0 || start.ActiveQueries != 1 || start.ActiveBindings != 1 {
		t.Fatal("final stream lost its original generated start identity or header")
	}
	end, finish, footer := rr[3], rr[4], rr[5]
	if end.Type != "query_end" || end.RequestID != start.RequestID || end.QueryID != start.QueryID || end.BackendPID != start.BackendPID || end.Error != failed || end.Complete == failed || end.ActiveQueries != 0 || end.ActiveBindings != 0 || end.counters != want ||
		finish.Type != "request_finish" || finish.RequestID != f.requestID || finish.Complete == failed || finish.counters != want || finish.ConnectionViolations != connectionViolations || finish.ActiveQueries != 0 || finish.ActiveBindings != 0 || finish.ActiveDriverOperations != 0 ||
		footer.Type != "footer" || footer.Complete == failed || footer.Error || footer.RequestID != "" || footer.QueryID != "" || footer.BackendPID != 0 || footer.counters != want || footer.ConnectionViolations != connectionViolations || footer.ActiveBindings != 0 || footer.ActiveQueries != 0 || footer.ActiveDriverOperations != 0 || footer.ActiveRequests != 0 || footer.RequestStarts != 1 || footer.RequestFinishes != 1 || footer.FinishCalls != 1 || footer.PostCloseEvents != 0 {
		t.Fatal("end attribution, exact counters, rejection, or final connection drain failed")
	}
	summaries, err := liveDecode[record](f.summary.snapshot())
	if err != nil || len(summaries) != 3 || summaries[0].Type != "header" {
		t.Fatal("summary stream is not the exact final three-record schema")
	}
	for i, row := range summaries[1:] {
		kind := "request"
		if i == 1 {
			kind = "footer"
		}
		if row.Type != kind || row.Version != 1 || row.RunID != f.run || row.Complete == failed || row.counters != want || row.RequestStarts != 1 || row.RequestFinishes != 1 || row.FinishCalls != 1 || row.Summaries != 1 || row.ActiveQueries != 0 || row.ActiveDriverOperations != 0 || row.ActiveRequests != 0 || row.PostCloseEvents != 0 {
			t.Fatal("summary final accounting, error rejection, or driver drain failed")
		}
	}
	if summaries[1].RequestID != f.requestID || summaries[1].Sequence != 1 || summaries[2].RequestID != "" {
		t.Fatal("summary and connection request identities disagree")
	}
	for _, p := range f.private {
		if p != "" && (bytes.Contains(f.events.snapshot(), []byte(p)) || bytes.Contains(f.summary.snapshot(), []byte(p))) {
			t.Fatal("private query, fixture, or error input entered diagnostic output (value suppressed)")
		}
	}
}

func TestConnectionEventsPostgreSQL(t *testing.T) {
	cfg := pgFixture(t) // The existing guarded DSN contract is unchanged; never skip.
	for _, cancellation := range []bool{false, true} {
		name := "blocked_start_rows_close"
		if cancellation {
			name = "blocked_cancellation_preserves_error"
		}
		t.Run(name, func(t *testing.T) {
			root, stop := context.WithTimeout(context.Background(), 30*time.Second)
			defer stop()
			controller := liveUnobservedConnection(t, cfg, root)
			monitor := liveUnobservedConnection(t, cfg, root)
			a, b, release := liveAdvisoryBlocker(t, root, controller)
			f, request := liveObserveConnection(t, cfg, root)
			await, cancel, assertPending := liveStartQuery(t, request, f, release, liveBlockedSQL, a, b)
			if !pgAwait(t, root, f.trace.starts) {
				t.Fatal("query start was not synchronously published before pgx tracer return")
			}
			start := f.beforeEnd(t)
			backendStart := liveAwaitAdvisoryWait(t, root, monitor, controller, f, start, a, b)
			f.beforeEnd(t) // Still blocked; no end/request summary is allowed.
			assertPending()
			if cancellation {
				cancel() // Only after independent proof of an in-flight lock wait.
			} else {
				release()
			}
			outcome := await()
			if cancellation {
				if outcome.rows != nil || !errors.Is(outcome.err, context.Canceled) || request.Err() != nil {
					t.Fatal("in-flight owned query cancellation outcome changed (details suppressed)")
				}
				release()
			} else {
				if outcome.err != nil || outcome.rows == nil {
					t.Fatal("released SELECT failed (details suppressed)")
				}
				var lockResult any
				var pid uint32
				var ordinal int
				if !outcome.rows.Next() || outcome.rows.Scan(&lockResult, &pid, &ordinal) != nil || pid != start.BackendPID || ordinal != 1 {
					t.Fatal("actual returned backend PID or first row disagrees with attribution")
				}
				// Leave the second row unread: the driver must retain the query
				// lifetime even if PostgreSQL has already buffered both rows.
				f.beforeEnd(t)
				var sameBackend bool
				if monitor.QueryRow(root, `SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_stat_activity WHERE pid = $1::int AND backend_start = $2)`, pid, backendStart).Scan(&sameBackend) != nil || !sameBackend {
					t.Fatal("workload backend incarnation changed before rows close")
				}
				if outcome.rows.Close() != nil || outcome.rows.Err() != nil {
					t.Fatal("owned rows close failed (details suppressed)")
				}
			}
			f.finalize(t, root, start, outcome.err)
			liveAwaitLocksReleased(t, root, monitor, a, b, controller.PgConn().PID(), f.pid)
		})
	}

	t.Run("server_error_preserves_original_error_and_rejects_finalization", func(t *testing.T) {
		root, stop := context.WithTimeout(context.Background(), 20*time.Second)
		defer stop()
		monitor := liveUnobservedConnection(t, cfg, root)
		f, request := liveObserveConnection(t, cfg, root)
		var backendStart time.Time
		if monitor.QueryRow(root, `SELECT backend_start FROM pg_catalog.pg_stat_activity WHERE pid = $1::int AND datname = current_database() AND usename = current_user`, f.pid).Scan(&backendStart) != nil || backendStart.IsZero() || f.pid == monitor.PgConn().PID() {
			t.Fatal("independent catalog could not identify the actual workload backend")
		}
		await, _, _ := liveStartQuery(t, request, f, func() {}, `SELECT $1::int`, pgPrivacy)
		if !pgAwait(t, root, f.trace.starts) {
			t.Fatal("server-error query start was not synchronously published")
		}
		outcome := await()
		var serverError *pgconn.PgError
		if outcome.rows != nil || !errors.As(outcome.err, &serverError) || serverError.Code != "22P02" || request.Err() != nil {
			t.Fatal("actual PostgreSQL error was replaced (details suppressed)")
		}
		f.private = append(f.private, serverError.Message, serverError.Detail, outcome.err.Error())
		rr, err := liveDecode[connectionRecord](f.events.snapshot())
		if err != nil || len(rr) != 4 || rr[2].Type != "query_start" || rr[2].BackendPID != f.pid {
			t.Fatal("server-error start did not use the catalog-identified backend")
		}
		var sameBackend bool
		if monitor.QueryRow(root, `SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_stat_activity WHERE pid = $1::int AND backend_start = $2)`, f.pid, backendStart).Scan(&sameBackend) != nil || !sameBackend {
			t.Fatal("server-error backend incarnation changed")
		}
		f.finalize(t, root, rr[2], outcome.err)
	})
}
