package database

import (
	"bytes"
	"context"
	"database/sql"
	"database/sql/driver"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/queryobservation"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
)

type observationKey struct{}
type observationEvent struct {
	kind  string
	ctx   context.Context
	err   error
	ended bool
}
type testDriverObserver struct {
	events []*observationEvent
	probes []context.Context
}

func (o *testDriverObserver) BeginDriverOperation(ctx context.Context, kind string) (context.Context, func(error)) {
	e := &observationEvent{kind: kind}
	c := context.WithValue(ctx, observationKey{}, e)
	e.ctx = c
	o.events = append(o.events, e)
	return c, func(err error) {
		if e.ended {
			panic("duplicate finish")
		}
		e.ended = true
		e.err = err
	}
}
func (o *testDriverObserver) ObserveResetProbe(ctx context.Context) { o.probes = append(o.probes, ctx) }

type observationConnector struct {
	connect func(context.Context) (driver.Conn, error)
	drv     driver.Driver
}

func (c observationConnector) Connect(ctx context.Context) (driver.Conn, error) {
	return c.connect(ctx)
}
func (c observationConnector) Driver() driver.Driver { return c.drv }

type observationNative struct {
	pgxDriver
	reset, ping func(context.Context) error
}

func (c *observationNative) ResetSession(ctx context.Context) error { return c.reset(ctx) }
func (c *observationNative) Ping(ctx context.Context) error         { return c.ping(ctx) }

func TestObservedShouldPingPreservesPinnedDefault(t *testing.T) {
	for _, idle := range []time.Duration{-time.Second, 0, time.Second - time.Nanosecond, time.Second, time.Second + time.Nanosecond, 2 * time.Second, time.Duration(1<<63 - 1)} {
		o := new(testDriverObserver)
		ctx := context.WithValue(context.Background(), observationKey{}, idle)
		got := observedShouldPing(o)(ctx, stdlib.ShouldPingParams{IdleDuration: idle})
		want := idle > time.Second
		if got != want || len(o.probes) != func() int {
			if want {
				return 1
			}
			return 0
		}() {
			t.Fatalf("idle=%s decision=%v probes=%d", idle, got, len(o.probes))
		}
		if want && o.probes[0] != ctx {
			t.Fatal("probe context changed")
		}
	}
}

func TestObservedConnectorAndOperationsPreserveContextsAndErrors(t *testing.T) {
	o := new(testDriverObserver)
	f := newTestFixture()
	parent, cancel := context.WithDeadline(context.Background(), time.Now().Add(time.Hour))
	defer cancel()
	wantErr := errors.New("private driver error")
	check := func(ctx context.Context, kind string) {
		t.Helper()
		e, ok := ctx.Value(observationKey{}).(*observationEvent)
		if !ok || e.kind != kind || e.ctx != ctx {
			t.Fatalf("missing derived %s context", kind)
		}
		p, _ := parent.Deadline()
		d, ok := ctx.Deadline()
		if !ok || d != p || ctx.Err() != parent.Err() {
			t.Fatal("deadline/cancellation changed")
		}
	}
	f.connection.pgxDriver = &observationNative{pgxDriver: f.connection.pgxDriver,
		reset: func(ctx context.Context) error { check(ctx, DriverOperationReset); return wantErr },
		ping:  func(ctx context.Context) error { check(ctx, DriverOperationPing); return wantErr },
	}
	c := &observedConnector{Connector: observationConnector{connect: func(ctx context.Context) (driver.Conn, error) {
		check(ctx, DriverOperationConnect)
		return f.connection, nil
	}}, observer: o}
	raw, err := c.Connect(parent)
	if err != nil {
		t.Fatal(err)
	}
	conn, ok := raw.(*observedConn)
	if !ok || conn.ownedConn != f.connection || !SupportsOwnedTransactions(raw) {
		t.Fatal("lost owned connection")
	}
	var _ pgxDriver = conn
	var _ driver.Validator = conn
	cancel()
	if err = conn.ResetSession(parent); err != wantErr {
		t.Fatal("reset error changed")
	}
	if err = conn.Ping(parent); err != wantErr {
		t.Fatal("ping error changed")
	}
	if len(o.events) != 3 || !o.events[0].ended || o.events[0].err != nil || o.events[1].err != wantErr || !o.events[1].ended || o.events[2].err != wantErr || !o.events[2].ended {
		t.Fatalf("events: %+v", o.events)
	}
	if !conn.IsValid() {
		t.Fatal("valid connection discarded")
	}
	if err = conn.Close(); err != nil || conn.IsValid() {
		t.Fatal("close validation changed")
	}
}

func TestObservedConnectFailureDoesNotExposeConnection(t *testing.T) {
	o := new(testDriverObserver)
	want := errors.New("connect failed")
	c := &observedConnector{Connector: observationConnector{connect: func(context.Context) (driver.Conn, error) { return nil, want }}, observer: o}
	conn, err := c.Connect(context.Background())
	if conn != nil || err != want || len(o.events) != 1 || !o.events[0].ended || o.events[0].err != want {
		t.Fatal("connect failure changed or omitted")
	}
}

func TestObservedOwnedTransactionsStillPublishAndDiscard(t *testing.T) {
	for _, failed := range []bool{false, true} {
		t.Run(func() string {
			if failed {
				return "failed_commit"
			}
			return "success"
		}(), func(t *testing.T) {
			o := new(testDriverObserver)
			f := newTestFixture()
			want := errors.New("commit failed")
			if failed {
				f.begin = func(context.Context, driver.TxOptions) (driver.Tx, error) {
					return testTx{commit: func() error { return want }, rollback: func() error { return nil }}, nil
				}
			}
			db := f.open(t, func(raw driver.Conn) driver.Conn { return &observedConn{ownedConn: raw.(*ownedConn), observer: o} })
			conn, err := db.Conn(context.Background())
			if err != nil {
				t.Fatal(err)
			}
			defer conn.Close()
			if err = conn.Raw(func(raw any) error {
				if !SupportsOwnedTransactions(raw) {
					return errors.New("marker lost")
				}
				return nil
			}); err != nil {
				t.Fatal(err)
			}
			ctx, completion := TrackTransaction(context.Background())
			tx, err := conn.BeginTx(ctx, nil)
			if err != nil {
				t.Fatal(err)
			}
			err = tx.Commit()
			if (failed && err != want) || (!failed && err != nil) {
				t.Fatal(err)
			}
			result := completion.Wait()
			if result.Discarded != failed || result.Err != err || f.closes.Load() != func() int32 {
				if failed {
					return 1
				}
				return 0
			}() {
				t.Fatalf("completion changed: %+v", result)
			}
			if failed {
				<-result.Cleanup
			}
		})
	}
}

func TestObservedDatabaseSQLRetriesRemainVisibleBeforeQuery(t *testing.T) {
	var out bytes.Buffer
	o, err := queryobservation.New(&out, "0a11bc22-3333-4444-8555-666677778888")
	if err != nil {
		t.Fatal(err)
	}
	ctx, _, finish := o.Begin(context.Background())
	attempts := 0
	db := sql.OpenDB(&observedConnector{Connector: observationConnector{connect: func(context.Context) (driver.Conn, error) { attempts++; return nil, driver.ErrBadConn }}, observer: o})
	defer db.Close()
	if _, err = db.ExecContext(ctx, "UNREACHED"); err != driver.ErrBadConn {
		t.Fatal(err)
	}
	finish()
	if !errors.Is(o.Close(), queryobservation.ErrIncomplete) || attempts != 3 {
		t.Fatalf("attempts=%d", attempts)
	}
	var footer map[string]any
	d := json.NewDecoder(&out)
	for d.More() {
		if err = d.Decode(&footer); err != nil {
			t.Fatal(err)
		}
	}
	if footer["connect_starts"] != float64(3) || footer["connect_ends"] != float64(3) || footer["connect_errors"] != float64(3) || footer["query_starts"] != float64(0) || footer["complete"] != false {
		t.Fatalf("footer: %+v", footer)
	}
}

func TestOpenObservedDBNilRetainsOwnedConnector(t *testing.T) {
	config, err := pgx.ParseConfig("")
	if err != nil {
		t.Fatal(err)
	}
	db := OpenObservedDB(*config, nil)
	defer db.Close()
	var _ DriverObserver = (*queryobservation.Observer)(nil)
	// OpenDB is lazy; constructing and closing both paths opens no socket.
	observed := OpenObservedDB(*config, new(testDriverObserver))
	defer observed.Close()
	if db.Stats().OpenConnections != 0 || observed.Stats().OpenConnections != 0 {
		t.Fatal("eager connection")
	}
}

type observationForwarder struct {
	pgxDriver
	t     *testing.T
	ctx   context.Context
	want  error
	calls int
}

func (c *observationForwarder) check(ctx context.Context) {
	c.t.Helper()
	if ctx != c.ctx {
		c.t.Fatal("unobserved operation context changed")
	}
	c.calls++
}
func (c *observationForwarder) PrepareContext(ctx context.Context, query string) (driver.Stmt, error) {
	c.check(ctx)
	if query != "prepare" {
		c.t.Fatal("query changed")
	}
	return nil, c.want
}
func (c *observationForwarder) ExecContext(ctx context.Context, query string, args []driver.NamedValue) (driver.Result, error) {
	c.check(ctx)
	if query != "exec" || len(args) != 1 || args[0].Value != "value" {
		c.t.Fatal("exec changed")
	}
	return nil, c.want
}
func (c *observationForwarder) QueryContext(ctx context.Context, query string, args []driver.NamedValue) (driver.Rows, error) {
	c.check(ctx)
	if query != "query" || len(args) != 1 || args[0].Value != "value" {
		c.t.Fatal("query changed")
	}
	return nil, c.want
}
func (c *observationForwarder) CheckNamedValue(v *driver.NamedValue) error {
	c.calls++
	v.Value = "checked"
	return c.want
}
func (c *observationForwarder) Conn() *pgx.Conn { return nil }

func TestObservedOptionalInterfacesForwardUnchanged(t *testing.T) {
	o := new(testDriverObserver)
	ctx := context.Background()
	want := errors.New("unchanged")
	f := &observationForwarder{t: t, ctx: ctx, want: want}
	c := &observedConn{ownedConn: &ownedConn{pgxDriver: f}, observer: o}
	args := []driver.NamedValue{{Ordinal: 1, Value: "value"}}
	if _, err := c.PrepareContext(ctx, "prepare"); err != want {
		t.Fatal(err)
	}
	if _, err := c.ExecContext(ctx, "exec", args); err != want {
		t.Fatal(err)
	}
	if _, err := c.QueryContext(ctx, "query", args); err != want {
		t.Fatal(err)
	}
	v := driver.NamedValue{}
	if err := c.CheckNamedValue(&v); err != want || v.Value != "checked" {
		t.Fatal("named value changed")
	}
	if f.calls != 4 || len(o.events) != 0 || c.Conn() != nil {
		t.Fatal("optional forwarding changed")
	}
	// Driver is forwarded by the outer connector too.
	drv := stdlib.GetDefaultDriver()
	connector := &observedConnector{Connector: observationConnector{drv: drv}, observer: o}
	if connector.Driver() != drv {
		t.Fatal("driver changed")
	}
}

func TestObservedResetFailureAndReconnectRetriesPrecedeQuery(t *testing.T) {
	var out bytes.Buffer
	o, err := queryobservation.New(&out, "0a11bc22-3333-4444-8555-666677778888")
	if err != nil {
		t.Fatal(err)
	}
	ctx, _, finish := o.Begin(context.Background())
	f := newTestFixture()
	f.connection.pgxDriver = &observationNative{pgxDriver: f.connection.pgxDriver,
		reset: func(ctx context.Context) error {
			observedShouldPing(o)(ctx, stdlib.ShouldPingParams{IdleDuration: 2 * time.Second})
			return driver.ErrBadConn
		},
	}
	attempts := 0
	db := sql.OpenDB(&observedConnector{Connector: observationConnector{connect: func(context.Context) (driver.Conn, error) {
		attempts++
		if attempts == 1 {
			return f.connection, nil
		}
		return nil, driver.ErrBadConn
	}}, observer: o})
	defer db.Close()
	conn, err := db.Conn(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if err = conn.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err = db.ExecContext(ctx, "UNREACHED"); err != driver.ErrBadConn {
		t.Fatal(err)
	}
	finish()
	if !errors.Is(o.Close(), queryobservation.ErrIncomplete) {
		t.Fatal("retry errors accepted")
	}
	var footer map[string]any
	d := json.NewDecoder(&out)
	for d.More() {
		if err = d.Decode(&footer); err != nil {
			t.Fatal(err)
		}
	}
	if attempts != 3 || f.closes.Load() != 1 || footer["connect_starts"] != float64(3) || footer["connect_errors"] != float64(2) || footer["reset_starts"] != float64(1) || footer["reset_ends"] != float64(1) || footer["reset_errors"] != float64(1) || footer["reset_probes"] != float64(1) || footer["query_starts"] != float64(0) || footer["active_driver_operations"] != float64(0) {
		t.Fatalf("retry accounting: %+v", footer)
	}
}

func TestObservedResetProbeAndExplicitPingRemainSeparate(t *testing.T) {
	var out bytes.Buffer
	o, err := queryobservation.New(&out, "0a11bc22-3333-4444-8555-666677778888")
	if err != nil {
		t.Fatal(err)
	}
	ctx, _, finish := o.Begin(context.Background())
	f := newTestFixture()
	idle := time.Second
	f.connection.pgxDriver = &observationNative{pgxDriver: f.connection.pgxDriver,
		reset: func(ctx context.Context) error {
			observedShouldPing(o)(ctx, stdlib.ShouldPingParams{IdleDuration: idle})
			return nil
		},
		ping: func(context.Context) error { return nil },
	}
	c := &observedConn{ownedConn: f.connection, observer: o}
	if err = c.ResetSession(ctx); err != nil {
		t.Fatal(err)
	}
	idle++
	if err = c.ResetSession(ctx); err != nil {
		t.Fatal(err)
	}
	if err = c.Ping(ctx); err != nil {
		t.Fatal(err)
	}
	finish()
	if err = o.Close(); err != nil {
		t.Fatal(err)
	}
	var footer map[string]any
	d := json.NewDecoder(&out)
	for d.More() {
		if err = d.Decode(&footer); err != nil {
			t.Fatal(err)
		}
	}
	if footer["reset_starts"] != float64(2) || footer["reset_ends"] != float64(2) || footer["reset_probes"] != float64(1) || footer["ping_starts"] != float64(1) || footer["ping_ends"] != float64(1) || footer["complete"] != true {
		t.Fatalf("probe/ping accounting: %+v", footer)
	}
}
