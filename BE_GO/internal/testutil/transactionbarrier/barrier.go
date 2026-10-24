// Package transactionbarrier exposes deterministic scheduling barriers around
// the real pgx driver for private integration fixtures. It never substitutes SQL
// or transaction outcomes. No production package imports this test utility.
package transactionbarrier

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"errors"
	"net"
	"sync"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

// Gate pauses one operation until Release, even if its context was cancelled.
// This models a permissible driver scheduling pause after database/sql has set
// Tx.done. Fixture cleanup must always release armed gates before closing DB.
type Gate struct {
	Entered <-chan struct{}
	entered chan struct{}
	release chan struct{}
	once    sync.Once
}

func newGate() *Gate {
	entered := make(chan struct{})
	return &Gate{Entered: entered, entered: entered, release: make(chan struct{})}
}
func (g *Gate) Release() { g.once.Do(func() { close(g.release) }) }
func (g *Gate) Wait(ctx context.Context) error {
	select {
	case <-g.Entered:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}
func (g *Gate) pause() { close(g.entered); <-g.release }

type Control struct {
	mu                     sync.Mutex
	begin, rollback, close *Gate
	gates                  []*Gate
}

func (c *Control) ArmBegin() *Gate {
	c.mu.Lock()
	defer c.mu.Unlock()
	g := newGate()
	c.begin = g
	c.gates = append(c.gates, g)
	return g
}
func (c *Control) takeBegin() *Gate {
	c.mu.Lock()
	defer c.mu.Unlock()
	g := c.begin
	c.begin = nil
	return g
}

func (c *Control) ArmRollback() *Gate {
	c.mu.Lock()
	defer c.mu.Unlock()
	g := newGate()
	c.rollback = g
	c.gates = append(c.gates, g)
	return g
}
func (c *Control) ArmClose() *Gate {
	c.mu.Lock()
	defer c.mu.Unlock()
	g := newGate()
	c.close = g
	c.gates = append(c.gates, g)
	return g
}
func (c *Control) takeRollback() *Gate {
	c.mu.Lock()
	defer c.mu.Unlock()
	g := c.rollback
	c.rollback = nil
	return g
}
func (c *Control) takeClose() *Gate {
	c.mu.Lock()
	defer c.mu.Unlock()
	g := c.close
	c.close = nil
	return g
}

// ReleaseAll is idempotent and permits private fixture shutdown after failures.
func (c *Control) ReleaseAll() {
	c.mu.Lock()
	gates := append([]*Gate(nil), c.gates...)
	c.mu.Unlock()
	for _, g := range gates {
		g.Release()
	}
}

func Open(dsn string) (*sqlx.DB, *Control, error) {
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		return nil, nil, errors.New("invalid private fixture database configuration")
	}
	return OpenConfig(cfg)
}

// OpenConfig retains a caller's real pgx tracer and configuration. The connector
// owns its physical connections, matching stdlib.OpenDB (not OpenDBFromPool).
func OpenConfig(original *pgx.ConnConfig) (*sqlx.DB, *Control, error) {
	if original == nil {
		return nil, nil, errors.New("private fixture database configuration required")
	}
	cfg := original.Copy()
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	control := &Control{}
	dial := cfg.DialFunc
	if dial == nil {
		dial = (&net.Dialer{}).DialContext
	}
	cfg.DialFunc = func(ctx context.Context, network, address string) (net.Conn, error) {
		conn, err := dial(ctx, network, address)
		if err != nil {
			return nil, err
		}
		return &wrappedNetwork{Conn: conn, control: control}, nil
	}
	db := sqlx.NewDb(sql.OpenDB(&wrappedConnector{Connector: stdlib.GetConnector(*cfg), control: control}), "pgx")
	db.SetMaxOpenConns(8)
	db.SetMaxIdleConns(8)
	return db, control, nil
}

type wrappedConnector struct {
	driver.Connector
	control *Control
}

func (c *wrappedConnector) Connect(ctx context.Context) (driver.Conn, error) {
	conn, err := c.Connector.Connect(ctx)
	if err != nil {
		return nil, err
	}
	actual, ok := conn.(*stdlib.Conn)
	if !ok {
		_ = conn.Close()
		return nil, errors.New("private barrier requires stdlib connector")
	}
	return &wrappedConn{NativeConn: actual, control: c.control}, nil
}

// The alias preserves every optional stdlib driver interface and exposes the
// promoted Conn() *pgx.Conn accessor to the production Raw cleanup boundary.
// Embedding *stdlib.Conn directly would shadow that accessor with a field.
type NativeConn = stdlib.Conn
type wrappedConn struct {
	*NativeConn
	control *Control
}

func (c *wrappedConn) BeginTx(ctx context.Context, opts driver.TxOptions) (driver.Tx, error) {
	if g := c.control.takeBegin(); g != nil {
		g.pause()
	}
	tx, err := c.NativeConn.BeginTx(ctx, opts)
	if err != nil {
		return nil, err
	}
	return &wrappedTx{Tx: tx, control: c.control}, nil
}
func (c *wrappedConn) Begin() (driver.Tx, error) {
	return c.BeginTx(context.Background(), driver.TxOptions{})
}

type wrappedTx struct {
	driver.Tx
	control *Control
}

func (t *wrappedTx) Rollback() error {
	if g := t.control.takeRollback(); g != nil {
		g.pause()
	}
	return t.Tx.Rollback()
}

type wrappedNetwork struct {
	net.Conn
	control *Control
}

func (c *wrappedNetwork) Close() error {
	if g := c.control.takeClose(); g != nil {
		g.pause()
	}
	return c.Conn.Close()
}

var _ driver.ConnBeginTx = (*wrappedConn)(nil)
var _ driver.ExecerContext = (*wrappedConn)(nil)
var _ driver.QueryerContext = (*wrappedConn)(nil)
var _ driver.ConnPrepareContext = (*wrappedConn)(nil)
var _ driver.Pinger = (*wrappedConn)(nil)
var _ driver.SessionResetter = (*wrappedConn)(nil)
var _ driver.NamedValueChecker = (*wrappedConn)(nil)
var _ interface{ Conn() *pgx.Conn } = (*wrappedConn)(nil)
