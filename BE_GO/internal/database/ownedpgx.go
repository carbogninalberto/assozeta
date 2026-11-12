// Package database supplies the owned pgx connector used beneath sqlx.
package database

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"errors"
	"sync"
	"sync/atomic"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
)

// OpenDB owns physical pgx connections. Pool-backed stdlib connections are not
// supported: their Close releases to another pool instead of closing the socket.
func OpenDB(config pgx.ConnConfig) *sql.DB {
	return sql.OpenDB(WrapConnector(stdlib.GetConnector(config)))
}

// WrapConnector also allows integration fixtures to place scheduling barriers
// around the real pgx driver. The connector must own its physical connections.
func WrapConnector(connector driver.Connector) driver.Connector {
	return &ownedConnector{Connector: connector}
}

type completionKey struct{}

var errCompletionReused = errors.New("transaction completion requires one sql.Conn.BeginTx call")

// Completion records driver completion, which occurs after sql.Tx becomes done.
// It contains no driver connection and is safe to retain after pool return.
type Completion struct {
	started atomic.Bool
	done    chan struct{}
	result  Result
}

type Result struct {
	Err       error
	CloseErr  error
	Discarded bool
	Cleanup   <-chan struct{}
}

// TrackTransaction preserves the caller's cancellation and deadline. Its result
// belongs to exactly one sql.Conn.BeginTx call. sql.DB.BeginTx is unsupported:
// it can retry driver BEGIN with the same context. Reuse is rejected before SQL.
func TrackTransaction(ctx context.Context) (context.Context, *Completion) {
	c := &Completion{done: make(chan struct{})}
	return context.WithValue(ctx, completionKey{}, c), c
}

// Started may be inspected after BeginTx returns, including when it fails before
// reaching the driver. Wait must only be called if Started reports true.
func (c *Completion) Started() bool { return c.started.Load() }
func (c *Completion) Wait() Result  { <-c.done; return c.result }

// SupportsOwnedTransactions is checked inside Conn.Raw before starting any
// transaction. There are no Raw calls concurrent with automatic rollback.
func SupportsOwnedTransactions(raw any) bool {
	_, ok := raw.(interface{ ownedTransactions() })
	return ok
}

// Embedding this interface preserves pgx's optional driver operations, including
// its session reset and named-argument handling, through fixture decorators.
type pgxDriver interface {
	driver.Conn
	driver.ConnBeginTx
	driver.ConnPrepareContext
	driver.ExecerContext
	driver.QueryerContext
	driver.Pinger
	driver.SessionResetter
	driver.NamedValueChecker
	Conn() *pgx.Conn
}

type ownedConnector struct{ driver.Connector }

func (c *ownedConnector) Connect(ctx context.Context) (driver.Conn, error) {
	raw, err := c.Connector.Connect(ctx)
	if err != nil {
		return nil, err
	}
	native, ok := raw.(pgxDriver)
	if !ok || native.Conn() == nil {
		_ = raw.Close()
		return nil, errors.New("owned transactions require a pgx connector")
	}
	return &ownedConn{pgxDriver: native, cleanup: native.Conn().PgConn().CleanupDone()}, nil
}

type ownedConn struct {
	pgxDriver
	cleanup   <-chan struct{}
	discarded atomic.Bool
	closeOnce sync.Once
	closeErr  error
}

func (*ownedConn) ownedTransactions() {}

func (c *ownedConn) Close() error {
	c.closeOnce.Do(func() {
		c.discarded.Store(true)
		c.closeErr = c.pgxDriver.Close()
	})
	return c.closeErr
}

// Validator prevents Conn.Close(nil) from returning a discarded connection to
// the pool even if it wins the race with sql.Tx's releaseConn(ErrBadConn).
func (c *ownedConn) IsValid() bool {
	if c.discarded.Load() {
		return false
	}
	if native := c.pgxDriver.Conn(); native != nil && native.IsClosed() {
		return false
	}
	if validator, ok := c.pgxDriver.(driver.Validator); ok {
		return validator.IsValid()
	}
	return true
}

func (c *ownedConn) Begin() (driver.Tx, error) {
	return c.BeginTx(context.Background(), driver.TxOptions{})
}

func (c *ownedConn) BeginTx(ctx context.Context, opts driver.TxOptions) (driver.Tx, error) {
	completion, _ := ctx.Value(completionKey{}).(*Completion)
	if completion == nil {
		completion = &Completion{done: make(chan struct{})}
	}
	if !completion.started.CompareAndSwap(false, true) {
		return nil, errCompletionReused
	}
	tx, err := c.pgxDriver.BeginTx(ctx, opts)
	if err != nil {
		c.complete(ctx, completion, err)
		return nil, err
	}
	return &ownedTx{Tx: tx, connection: c, ctx: ctx, completion: completion}, nil
}

// complete runs inside the database/sql driver call, while the driver connection
// is exclusively held. No driver pointer escapes Raw or is used after pool return.
// Driver Close may initiate asynchronous pgx cleanup; the caller joins Cleanup.
func (c *ownedConn) complete(ctx context.Context, completion *Completion, err error) {
	if err != nil || ctx.Err() != nil {
		_ = c.Close() // Preserve the transaction error; Cleanup joins socket teardown.
	}
	completion.result = Result{Err: err, CloseErr: c.closeErr, Discarded: c.discarded.Load(), Cleanup: c.cleanup}
	close(completion.done)
}

type ownedTx struct {
	driver.Tx
	connection *ownedConn
	ctx        context.Context
	completion *Completion
}

func (t *ownedTx) Commit() error {
	err := t.Tx.Commit()
	t.connection.complete(t.ctx, t.completion, err)
	return err
}

func (t *ownedTx) Rollback() error {
	err := t.Tx.Rollback()
	t.connection.complete(t.ctx, t.completion, err)
	return err
}

var _ driver.Validator = (*ownedConn)(nil)
var _ pgxDriver = (*ownedConn)(nil)
