//go:build queryobservationintegration

package queryobservation

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

const pgPrivacy = "query_observer_private_parameter_7429"

type pgPrivacyKey struct{}

// The parent owns this disposable fixture. This test never starts PostgreSQL,
// creates schema, or falls back to another database when configuration is absent.
func pgFixture(t *testing.T) *pgx.ConnConfig {
	t.Helper()
	dsn := os.Getenv("ASSOZETA_QUERY_OBSERVATION_DSN")
	if strings.TrimSpace(dsn) == "" {
		t.Fatal("ASSOZETA_QUERY_OBSERVATION_DSN must explicitly select the parent-owned fixture")
	}
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal("cannot parse fixture DSN (details suppressed)")
	}
	if cfg.Database != "assozeta_query_observation" || cfg.User != "contracts" {
		t.Fatal("fixture DSN must select the exact private database and application role")
	}
	checkSocket := func(host string) {
		if !filepath.IsAbs(host) || !strings.HasPrefix(filepath.Base(host), "az-query-") {
			t.Fatal("fixture requires an absolute az-query-* Unix socket directory")
		}
		info, err := os.Lstat(host)
		if err != nil || !info.IsDir() || info.Mode().Perm() != 0700 {
			t.Fatal("fixture socket directory must be a private 0700 directory")
		}
	}
	checkSocket(cfg.Host)
	for _, fallback := range cfg.Fallbacks {
		checkSocket(fallback.Host)
	}
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	cfg.ConnectTimeout = 5 * time.Second

	// Validate before installing the observer so fixture checks never enter the
	// measured workload. Only SELECTs are used, including this catalog check.
	db := pgOpen(t, cfg)
	defer db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	var valid bool
	err = db.QueryRowContext(ctx, `SELECT
		current_database() = 'assozeta_query_observation'
		AND current_user = 'contracts' AND session_user = 'contracts'
		AND inet_server_addr() IS NULL AND d.datdba = r.oid
		AND r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreatedb
		AND NOT r.rolcreaterole AND NOT r.rolreplication AND NOT r.rolbypassrls
		FROM pg_catalog.pg_roles r JOIN pg_catalog.pg_database d
		ON d.datname = current_database() WHERE r.rolname = current_user`).Scan(&valid)
	if err != nil || !valid {
		t.Fatal("live fixture database, role, ownership, or Unix transport contract failed (details suppressed)")
	}
	return cfg
}

func pgOpen(t *testing.T, cfg *pgx.ConnConfig) *sql.DB {
	t.Helper()
	db := database.OpenDB(*cfg.Copy())
	db.SetMaxOpenConns(3)
	db.SetMaxIdleConns(3)
	t.Cleanup(func() { _ = db.Close() })
	return db
}

type pgObservation struct {
	observer *Observer
	db       *sql.DB
	output   bytes.Buffer
	run      string
	private  []string
}

func pgObserve(t *testing.T, cfg *pgx.ConnConfig) *pgObservation {
	t.Helper()
	f := &pgObservation{run: uuid.NewString(), private: []string{
		pgPrivacy, os.Getenv("ASSOZETA_QUERY_OBSERVATION_DSN"), cfg.Host, cfg.Password, "SELECT", "pg_sleep",
	}}
	var err error
	f.observer, err = New(&f.output, f.run)
	if err != nil {
		t.Fatal("cannot construct observer")
	}
	observed := cfg.Copy()
	observed.Tracer = f.observer
	f.db = pgOpen(t, observed)
	t.Cleanup(func() { _ = f.db.Close(); _ = f.observer.Close() })
	return f
}

type pgWant struct{ queries, errors uint64 }

type pgWindow struct{ start, end time.Time }

// All output is inspected only after workers and rows have drained, then the
// real database/sql pool is closed, and finally the observer writes its footer.
func (f *pgObservation) check(t *testing.T, want map[string]pgWant) {
	t.Helper()
	if f.db.Stats().InUse != 0 {
		t.Fatal("database work did not drain")
	}
	if f.db.Close() != nil {
		t.Fatal("database close failed (details suppressed)")
	}
	var totals counters
	for _, w := range want {
		totals.QueryStarts += w.queries
		totals.QueryEnds += w.queries
		totals.QueryErrors += w.errors
	}
	err := f.observer.Close()
	if totals.QueryErrors == 0 && err != nil || totals.QueryErrors != 0 && !errors.Is(err, ErrIncomplete) {
		t.Fatal("observer finalization did not reflect query outcomes")
	}
	for _, private := range f.private {
		if private != "" && bytes.Contains(f.output.Bytes(), []byte(private)) {
			t.Fatal("diagnostics leaked private input (value suppressed)")
		}
	}
	decoder := json.NewDecoder(bytes.NewReader(f.output.Bytes()))
	decoder.DisallowUnknownFields() // Reject additional fields such as SQL/arguments/errors.
	var rr []record
	for {
		var r record
		if err := decoder.Decode(&r); err == io.EOF {
			break
		} else if err != nil {
			t.Fatal("diagnostics are not the fixed JSONL schema (details suppressed)")
		}
		if r.Version != 1 || r.RunID != f.run {
			t.Fatal("invalid diagnostic version or run ID")
		}
		rr = append(rr, r)
	}
	if len(rr) != len(want)+2 || rr[0].Type != "header" || rr[0].Complete || rr[0].counters != (counters{}) {
		t.Fatal("missing header or unexpected diagnostic record count")
	}
	seen := make(map[string]bool)
	for _, r := range rr[1 : len(rr)-1] {
		w, ok := want[r.RequestID]
		if !ok || seen[r.RequestID] || r.Type != "request" || r.Sequence == 0 ||
			r.RequestID != f.run+"-"+strconv.FormatUint(r.Sequence, 10) ||
			r.Complete != (w.errors == 0) || r.ActiveQueries != 0 ||
			r.counters != (counters{QueryStarts: w.queries, QueryEnds: w.queries, QueryErrors: w.errors}) ||
			r.RequestStarts != 1 || r.RequestFinishes != 1 || r.FinishCalls != 1 || r.Summaries != 1 {
			t.Fatal("request attribution, unique generated ID, completion, or exact counters failed")
		}
		seen[r.RequestID] = true
	}
	footer := rr[len(rr)-1]
	n := uint64(len(want))
	if footer.Type != "footer" || footer.Complete != (totals.QueryErrors == 0) || footer.counters != totals ||
		footer.RequestStarts != n || footer.RequestFinishes != n || footer.FinishCalls != n || footer.Summaries != n ||
		footer.ActiveRequests != 0 || footer.ActiveQueries != 0 || footer.PostCloseEvents != 0 {
		t.Fatal("last record is not the expected drained footer")
	}
}

func pgAwait[T any](t *testing.T, ctx context.Context, ch <-chan T) T {
	t.Helper()
	select {
	case value := <-ch:
		return value
	case <-ctx.Done():
		t.Fatal("integration barrier timed out")
		var zero T
		return zero
	}
}

func TestQueryObservationPostgreSQL(t *testing.T) {
	cfg := pgFixture(t) // Missing DSN is a failure, never a skip.
	t.Run("concurrent_requests_and_rows_close", func(t *testing.T) {
		f := pgObserve(t, cfg)
		root, cancel := context.WithTimeout(context.WithValue(context.Background(), pgPrivacyKey{}, pgPrivacy), 30*time.Second)
		defer cancel()
		ready, connected := make(chan struct{}, 8), make(chan struct{}, 8)
		held := make(chan pgWindow, 8)
		start, execute, releaseRows := make(chan struct{}), make(chan struct{}), make(chan struct{})
		results := make(chan bool, 8)
		var wg sync.WaitGroup
		var releaseOnce sync.Once
		release := func() { releaseOnce.Do(func() { close(releaseRows) }) }
		defer func() { cancel(); release(); wg.Wait() }()
		want := make(map[string]pgWant)
		for i := 0; i < 8; i++ {
			ctx, id, finish := f.observer.Begin(root)
			if _, duplicate := want[id]; duplicate {
				t.Fatal("Begin reused a generated request ID")
			}
			want[id] = pgWant{queries: 3}
			wg.Add(1)
			go func(i int) {
				defer wg.Done()
				defer finish()
				ready <- struct{}{}
				select {
				case <-start:
				case <-ctx.Done():
					results <- false
					return
				}
				conn, err := f.db.Conn(ctx)
				if err != nil {
					results <- false
					return
				}
				defer conn.Close()
				connected <- struct{}{}
				select {
				case <-execute:
				case <-ctx.Done():
					results <- false
					return
				}
				for j := 0; j < 3; j++ {
					// Two rows deliberately leave an unread row for explicit Close.
					// pg_sleep creates work; channels, not elapsed time, gate scheduling.
					// Volatile CTEs retain the server timestamps around the sleep.
					rows, err := conn.QueryContext(ctx, `WITH begun AS (SELECT clock_timestamp() AS began),
						worked AS (SELECT began, pg_sleep($4::double precision) FROM begun)
						SELECT $1::int + $2::int + g - 1, $3::text, pg_backend_pid(),
						began, clock_timestamp() FROM worked, generate_series(1, 2) g`, i, j, pgPrivacy, 0.05)
					if err != nil {
						results <- false
						return
					}
					var value, pid int
					var private string
					var window pgWindow
					ok := rows.Next() && rows.Scan(&value, &private, &pid, &window.start, &window.end) == nil &&
						value == i+j && private == pgPrivacy && pid > 0 && window.start.Before(window.end)
					if j == 0 {
						held <- window
						select {
						case <-releaseRows:
						case <-ctx.Done():
							_ = rows.Close()
							results <- false
							return
						}
					}
					closeErr := rows.Close()
					if !ok || closeErr != nil || rows.Err() != nil {
						results <- false
						return
					}
				}
				results <- true
			}(i)
		}
		for range 8 {
			pgAwait(t, root, ready)
		}
		close(start)
		for range 3 {
			pgAwait(t, root, connected)
		}
		close(execute) // First three physical connections are ready before SQL starts.
		var windows []pgWindow
		for range 3 {
			windows = append(windows, pgAwait(t, root, held))
		}
		overlapped := false
		for i, a := range windows {
			for _, b := range windows[i+1:] {
				overlapped = overlapped || a.start.Before(b.end) && b.start.Before(a.end)
			}
		}
		if !overlapped {
			t.Error("server execution intervals did not overlap")
		}
		// Three real driver row lifetimes overlap on the three leased connections.
		// No end is allowed before rows are closed, even when results are buffered.
		f.observer.mu.Lock()
		active, starts, ends := f.observer.activeQueries, f.observer.counts.QueryStarts, f.observer.counts.QueryEnds
		f.observer.mu.Unlock()
		if active != 3 || starts != 3 || ends != 0 || f.db.Stats().InUse != 3 {
			t.Error("real rows did not overlap or tracer wiring/rows-close lifetime is incorrect")
		}
		release()
		for range 8 {
			if !pgAwait(t, root, results) {
				t.Error("parameterized SELECT or known result check failed (details suppressed)")
			}
		}
		wg.Wait()        // Results can arrive just before deferred request finishes.
		f.check(t, want) // Exactly eight distinct 3/3/0 requests and 24/24/0 total.
	})

	for _, cancellation := range []bool{false, true} {
		name := "sql_error_attribution"
		if cancellation {
			name = "cancellation_attribution"
		}
		t.Run(name, func(t *testing.T) {
			f := pgObserve(t, cfg)
			root, stop := context.WithTimeout(context.Background(), 20*time.Second)
			defer stop()
			badRoot, cancel := context.WithCancel(root)
			defer cancel()
			bad, badID, finishBad := f.observer.Begin(badRoot)
			good, goodID, finishGood := f.observer.Begin(root)
			var value int
			if f.db.QueryRowContext(good, `SELECT $1::int`, 42).Scan(&value) != nil || value != 42 {
				t.Fatal("healthy sibling SELECT failed (details suppressed)")
			}
			finishGood()
			if cancellation {
				// A large, paced result flushes a real row before cancellation. The
				// server cannot finish the entire stream within the request timeout.
				rows, err := f.db.QueryContext(bad, `SELECT $1::int, repeat($2::text, 16384),
					pg_sleep(0.001) FROM generate_series(1, 100000)`, 7, pgPrivacy)
				if err != nil {
					t.Fatal("streaming SELECT failed before cancellation (details suppressed)")
				}
				defer rows.Close()
				var payload, delay string
				if !rows.Next() || rows.Scan(&value, &payload, &delay) != nil || value != 7 || payload != strings.Repeat(pgPrivacy, 16384) {
					t.Fatal("streaming SELECT did not yield its known first row")
				}
				cancel() // Cancellation is after a real result, never before driver entry.
				for rows.Next() {
				}
				_ = rows.Close()
				if !errors.Is(rows.Err(), context.Canceled) {
					t.Fatal("in-flight SELECT did not report cancellation (details suppressed)")
				}
			} else {
				err := f.db.QueryRowContext(bad, `SELECT $1::int`, pgPrivacy).Scan(&value)
				var serverError *pgconn.PgError
				if !errors.As(err, &serverError) || serverError.Code != "22P02" {
					t.Fatal("SELECT did not return the expected real PostgreSQL error (details suppressed)")
				}
				f.private = append(f.private, serverError.Message, serverError.Detail)
			}
			finishBad()
			f.check(t, map[string]pgWant{goodID: {queries: 1}, badID: {queries: 1, errors: 1}})
		})
	}

	t.Run("transport_ping_is_outside_query_tracer", func(t *testing.T) {
		f := pgObserve(t, cfg)
		root, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		ctx, id, finish := f.observer.Begin(root)
		if f.db.PingContext(ctx) != nil {
			t.Fatal("real transport Ping failed (details suppressed)")
		}
		finish()
		f.check(t, map[string]pgWant{id: {}}) // Successful transport probe, ZERO covered SQL.
	})
}
