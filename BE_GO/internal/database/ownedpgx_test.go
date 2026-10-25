package database

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

// These tests control the database/sql/driver boundary without a PostgreSQL
// substitute for business behavior. Real SQL, socket cleanup and restore leases
// are covered by the separate PostgreSQL transaction-lifetime integration suite.
type testConnector struct{ connect func() driver.Conn }

func (c testConnector) Connect(context.Context) (driver.Conn, error) { return c.connect(), nil }
func (testConnector) Driver() driver.Driver                          { return nil }

type testNative struct {
	pgxDriver
	begin func(context.Context, driver.TxOptions) (driver.Tx, error)
	close func() error
}

func (*testNative) Conn() *pgx.Conn { return nil }
func (c *testNative) BeginTx(ctx context.Context, opts driver.TxOptions) (driver.Tx, error) {
	return c.begin(ctx, opts)
}
func (c *testNative) Close() error                     { return c.close() }
func (*testNative) ResetSession(context.Context) error { return nil }

type testTx struct {
	commit, rollback func() error
}

func (t testTx) Commit() error   { return t.commit() }
func (t testTx) Rollback() error { return t.rollback() }

type testFixture struct {
	connection       *ownedConn
	cleanup          chan struct{}
	closes, connects atomic.Int32
	cleanupOnce      sync.Once
	begin            func(context.Context, driver.TxOptions) (driver.Tx, error)
	close            func() error
}

func newTestFixture() *testFixture {
	f := &testFixture{cleanup: make(chan struct{})}
	f.begin = func(context.Context, driver.TxOptions) (driver.Tx, error) {
		return testTx{commit: func() error { return nil }, rollback: func() error { return nil }}, nil
	}
	f.close = func() error { f.finishCleanup(); return nil }
	f.connection = &ownedConn{cleanup: f.cleanup, pgxDriver: &testNative{
		begin: func(ctx context.Context, opts driver.TxOptions) (driver.Tx, error) { return f.begin(ctx, opts) },
		close: func() error { f.closes.Add(1); return f.close() },
	}}
	return f
}

func (f *testFixture) finishCleanup() { f.cleanupOnce.Do(func() { close(f.cleanup) }) }
func (f *testFixture) open(t *testing.T, decorate func(driver.Conn) driver.Conn) *sql.DB {
	t.Helper()
	db := sql.OpenDB(testConnector{connect: func() driver.Conn {
		f.connects.Add(1)
		if decorate != nil {
			return decorate(f.connection)
		}
		return f.connection
	}})
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	t.Cleanup(func() { f.finishCleanup(); _ = db.Close() })
	return db
}

func awaitTest[T any](t *testing.T, ch <-chan T) T {
	t.Helper()
	select {
	case value := <-ch:
		return value
	case <-time.After(3 * time.Second):
		t.Fatal("driver boundary timed out")
		var zero T
		return zero
	}
}
func pendingTest[T any](t *testing.T, ch <-chan T) {
	t.Helper()
	select {
	case <-ch:
		t.Fatal("operation escaped the controlled driver boundary")
	case <-time.After(20 * time.Millisecond):
	}
}

// A fixture decorator pauses after the owned driver's notification but before
// database/sql releases its transaction connection lease.
type releaseConnBarrier struct {
	*ownedConn
	entered, release chan struct{}
}

func (c *releaseConnBarrier) BeginTx(ctx context.Context, opts driver.TxOptions) (driver.Tx, error) {
	tx, err := c.ownedConn.BeginTx(ctx, opts)
	if err != nil {
		return nil, err
	}
	return releaseTxBarrier{Tx: tx, entered: c.entered, release: c.release}, nil
}

type releaseTxBarrier struct {
	driver.Tx
	entered, release chan struct{}
}

func (t releaseTxBarrier) Rollback() error {
	err := t.Tx.Rollback()
	close(t.entered)
	<-t.release
	return err
}

func TestCompletionBeforeSQLReleaseAndConcurrentTerminalCaller(t *testing.T) {
	f := newTestFixture()
	entered, release := make(chan struct{}), make(chan struct{})
	var once sync.Once
	unblock := func() { once.Do(func() { close(release) }) }
	defer unblock()
	db := f.open(t, func(driver.Conn) driver.Conn {
		return &releaseConnBarrier{ownedConn: f.connection, entered: entered, release: release}
	})
	conn, err := db.Conn(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	ctx, completion := TrackTransaction(context.Background())
	tx, err := conn.BeginTx(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	first := make(chan error, 1)
	go func() { first <- tx.Rollback() }()
	awaitTest(t, entered)
	result := completion.Wait()
	if result.Err != nil || result.Discarded {
		t.Fatal("healthy rollback was discarded", result)
	}
	if err := tx.Rollback(); !errors.Is(err, sql.ErrTxDone) {
		t.Fatal("concurrent terminal result", err)
	}
	closed := make(chan error, 1)
	go func() { closed <- conn.Close() }()
	pendingTest(t, closed)
	pendingTest(t, first)
	unblock()
	if err := awaitTest(t, first); err != nil {
		t.Fatal(err)
	}
	if err := awaitTest(t, closed); err != nil {
		t.Fatal(err)
	}
	if db.Stats().Idle != 1 || f.closes.Load() != 0 {
		t.Fatal("healthy connection was not reused")
	}
	// Notification also remains readable after SQL's release, without driver access.
	if result := completion.Wait(); result.Discarded {
		t.Fatal("completion changed after release")
	}
	conn, err = db.Conn(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	_ = conn.Close()
	if f.connects.Load() != 1 {
		t.Fatal("healthy connection was replaced")
	}
}

func TestCancellationJoinsDriverThenPhysicalCleanup(t *testing.T) {
	f := newTestFixture()
	rollbackEntered, releaseRollback := make(chan struct{}), make(chan struct{})
	var once sync.Once
	unblock := func() { once.Do(func() { close(releaseRollback) }) }
	defer unblock()
	var commits atomic.Int32
	f.begin = func(ctx context.Context, opts driver.TxOptions) (driver.Tx, error) {
		if opts.Isolation != driver.IsolationLevel(sql.LevelRepeatableRead) || !opts.ReadOnly {
			t.Error("transaction options changed")
		}
		return testTx{commit: func() error { commits.Add(1); return nil }, rollback: func() error {
			close(rollbackEntered)
			<-releaseRollback
			return ctx.Err()
		}}, nil
	}
	f.close = func() error { return nil } // Physical cleanup has its own acknowledgment.
	db := f.open(t, nil)
	conn, err := db.Conn(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	work, cancel := context.WithCancel(context.Background())
	defer cancel()
	ctx, completion := TrackTransaction(work)
	tx, err := conn.BeginTx(ctx, &sql.TxOptions{Isolation: sql.LevelRepeatableRead, ReadOnly: true})
	if err != nil {
		t.Fatal(err)
	}
	cancel()
	awaitTest(t, rollbackEntered)
	if err := tx.Commit(); err == nil {
		t.Fatal("commit accepted cancellation")
	}
	if err := tx.Rollback(); !errors.Is(err, sql.ErrTxDone) {
		t.Fatal(err)
	}
	joined := make(chan Result, 1)
	go func() {
		result := completion.Wait()
		if result.Discarded {
			<-result.Cleanup
		}
		_ = conn.Close()
		joined <- result
	}()
	pendingTest(t, joined)
	unblock()
	result := completion.Wait()
	if !result.Discarded || !errors.Is(result.Err, context.Canceled) || f.closes.Load() != 1 {
		t.Fatal("cancellation was not discarded", result)
	}
	pendingTest(t, joined)
	f.finishCleanup()
	awaitTest(t, joined)
	if commits.Load() != 0 || db.Stats().InUse != 0 || db.Stats().Idle != 0 {
		t.Fatal("cancelled transaction was committed or retained")
	}
}

func TestFailedBeginPublishesCleanupAndOriginalError(t *testing.T) {
	f := newTestFixture()
	want := errors.New("begin failed")
	f.begin = func(context.Context, driver.TxOptions) (driver.Tx, error) { return nil, want }
	f.close = func() error { return nil }
	db := f.open(t, nil)
	conn, err := db.Conn(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	ctx, completion := TrackTransaction(context.Background())
	if _, err = conn.BeginTx(ctx, nil); !errors.Is(err, want) {
		t.Fatal("begin error was masked", err)
	}
	if !completion.Started() {
		t.Fatal("missing begin notification")
	}
	result := completion.Wait()
	if !errors.Is(result.Err, want) || !result.Discarded {
		t.Fatal(result)
	}
	pendingTest(t, result.Cleanup)
	f.finishCleanup()
	_ = conn.Close()
	if f.closes.Load() != 1 || db.Stats().Idle != 0 {
		t.Fatal("failed begin connection was retained")
	}
}

func TestBeginNeverEnteredDoesNotWaitForCompletion(t *testing.T) {
	f := newTestFixture()
	db := f.open(t, nil)
	conn, err := db.Conn(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if err = conn.Close(); err != nil {
		t.Fatal(err)
	}
	ctx, completion := TrackTransaction(context.Background())
	if _, err = conn.BeginTx(ctx, nil); !errors.Is(err, sql.ErrConnDone) {
		t.Fatal(err)
	}
	if completion.Started() {
		t.Fatal("driver began a transaction on a closed sql.Conn")
	}
	if f.closes.Load() != 0 || db.Stats().Idle != 1 {
		t.Fatal("unstarted transaction discarded a pooled connection")
	}
}

func TestFailedCommitDiscardsOnceAndPreservesError(t *testing.T) {
	for _, want := range []error{errors.New("ambiguous commit"), driver.ErrBadConn} {
		t.Run(want.Error(), func(t *testing.T) {
			f := newTestFixture()
			f.begin = func(context.Context, driver.TxOptions) (driver.Tx, error) {
				return testTx{commit: func() error { return want }, rollback: func() error { t.Error("driver rollback after terminal commit"); return nil }}, nil
			}
			db := f.open(t, nil)
			conn, err := db.Conn(context.Background())
			if err != nil {
				t.Fatal(err)
			}
			ctx, completion := TrackTransaction(context.Background())
			tx, err := conn.BeginTx(ctx, nil)
			if err != nil {
				t.Fatal(err)
			}
			if err = tx.Commit(); !errors.Is(err, want) {
				t.Fatal("commit error was masked", err)
			}
			result := completion.Wait()
			if !result.Discarded || !errors.Is(result.Err, want) {
				t.Fatal(result)
			}
			awaitTest(t, result.Cleanup)
			_ = conn.Close()
			if f.closes.Load() != 1 || db.Stats().InUse != 0 || db.Stats().Idle != 0 {
				t.Fatal("failed commit escaped physical disposal")
			}
		})
	}
}

func TestSuccessfulCommitReusesConnection(t *testing.T) {
	f := newTestFixture()
	db := f.open(t, nil)
	for range 2 {
		conn, err := db.Conn(context.Background())
		if err != nil {
			t.Fatal(err)
		}
		ctx, completion := TrackTransaction(context.Background())
		tx, err := conn.BeginTx(ctx, nil)
		if err != nil {
			t.Fatal(err)
		}
		if err = tx.Commit(); err != nil {
			t.Fatal(err)
		}
		if result := completion.Wait(); result.Discarded || result.Err != nil {
			t.Fatal(result)
		}
		if err = conn.Close(); err != nil {
			t.Fatal(err)
		}
	}
	if f.connects.Load() != 1 || f.closes.Load() != 0 || db.Stats().Idle != 1 {
		t.Fatal("successful commits lost pool reuse")
	}
}

func TestCancellationAfterAcknowledgedCommitPreservesSuccess(t *testing.T) {
	for _, beforeNotification := range []bool{true, false} {
		name := "after notification"
		if beforeNotification {
			name = "before notification"
		}
		t.Run(name, func(t *testing.T) {
			f := newTestFixture()
			work, cancel := context.WithCancel(context.Background())
			defer cancel()
			f.begin = func(context.Context, driver.TxOptions) (driver.Tx, error) {
				return testTx{commit: func() error {
					// The driver has already acknowledged COMMIT when cancellation arrives.
					if beforeNotification {
						cancel()
					}
					return nil
				}, rollback: func() error { t.Error("acknowledged commit was rolled back"); return nil }}, nil
			}
			db := f.open(t, nil)
			conn, err := db.Conn(context.Background())
			if err != nil {
				t.Fatal(err)
			}
			ctx, completion := TrackTransaction(work)
			tx, err := conn.BeginTx(ctx, nil)
			if err != nil {
				t.Fatal(err)
			}
			if err = tx.Commit(); err != nil {
				t.Fatal("acknowledged commit became an error", err)
			}
			if !beforeNotification {
				cancel()
			}
			result := completion.Wait()
			if result.Err != nil || result.Discarded != beforeNotification {
				t.Fatal("post-commit cancellation changed terminal outcome", result)
			}
			if result.Discarded {
				awaitTest(t, result.Cleanup)
			}
			_ = conn.Close()
			if beforeNotification && db.Stats().Idle != 0 {
				t.Fatal("cancelled connection returned to pool")
			}
			if !beforeNotification && db.Stats().Idle != 1 {
				t.Fatal("late cancellation discarded an acknowledged transaction")
			}
		})
	}
}

func TestTrackedDBBeginRetryFailsBeforeSecondDriverBegin(t *testing.T) {
	var connects, begins atomic.Int32
	db := sql.OpenDB(testConnector{connect: func() driver.Conn {
		connects.Add(1)
		f := newTestFixture()
		f.begin = func(context.Context, driver.TxOptions) (driver.Tx, error) {
			begins.Add(1)
			return nil, driver.ErrBadConn
		}
		return f.connection
	}})
	defer db.Close()
	ctx, completion := TrackTransaction(context.Background())
	// Unlike the supported sql.Conn.BeginTx path, DB.BeginTx retries internally.
	// The retry must fail before another native BEGIN or a second notification.
	if _, err := db.BeginTx(ctx, nil); !errors.Is(err, errCompletionReused) {
		t.Fatal("tracked database retry was not rejected", err)
	}
	if connects.Load() != 2 || begins.Load() != 1 {
		t.Fatal("completion reuse reached another driver BEGIN", connects.Load(), begins.Load())
	}
	result := completion.Wait()
	if !errors.Is(result.Err, driver.ErrBadConn) || !result.Discarded {
		t.Fatal("retry overwrote the original terminal result", result)
	}
	awaitTest(t, result.Cleanup)
	if db.Stats().InUse != 0 {
		t.Fatal("rejected retry leaked its connection")
	}
}
