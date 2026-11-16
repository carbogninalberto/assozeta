package repository

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"fmt"
	"io"
	"sync"
	"testing"

	"github.com/jmoiron/sqlx"
)

// rowRaceDriver is a database/sql driver for the units that read a row, find it
// gone and must answer what Django answers. Each test scripts the statements
// and records them; anything unscripted fails, as for message_reads_test.go.
type rowRaceDriver struct {
	mu      sync.Mutex
	queries []rowRaceCall
	execs   []rowRaceCall
	queryFn func(string, []driver.NamedValue) ([]string, [][]driver.Value, error)
	execFn  func(string, []driver.NamedValue) (driver.Result, error)
}

type rowRaceCall struct {
	query string
	args  []driver.NamedValue
}

func (d *rowRaceDriver) Open(string) (driver.Conn, error) { return &rowRaceConn{d: d}, nil }

type rowRaceConn struct{ d *rowRaceDriver }

func (c *rowRaceConn) Prepare(string) (driver.Stmt, error) { return nil, driver.ErrSkip }
func (c *rowRaceConn) Close() error                        { return nil }
func (c *rowRaceConn) Begin() (driver.Tx, error)           { return rowRaceTx{}, nil }

func (c *rowRaceConn) BeginTx(context.Context, driver.TxOptions) (driver.Tx, error) {
	return rowRaceTx{}, nil
}

func (c *rowRaceConn) QueryContext(_ context.Context, query string, args []driver.NamedValue) (driver.Rows, error) {
	c.d.mu.Lock()
	c.d.queries = append(c.d.queries, rowRaceCall{query: query, args: append([]driver.NamedValue(nil), args...)})
	c.d.mu.Unlock()
	if c.d.queryFn == nil {
		return nil, fmt.Errorf("unexpected query: %s", query)
	}
	columns, values, err := c.d.queryFn(query, args)
	if err != nil {
		return nil, err
	}
	return &rowRaceRows{columns: columns, values: values}, nil
}

func (c *rowRaceConn) ExecContext(_ context.Context, query string, args []driver.NamedValue) (driver.Result, error) {
	c.d.mu.Lock()
	c.d.execs = append(c.d.execs, rowRaceCall{query: query, args: append([]driver.NamedValue(nil), args...)})
	c.d.mu.Unlock()
	if c.d.execFn == nil {
		return nil, fmt.Errorf("unexpected exec: %s", query)
	}
	return c.d.execFn(query, args)
}

type rowRaceTx struct{}

func (rowRaceTx) Commit() error   { return nil }
func (rowRaceTx) Rollback() error { return nil }

type rowRaceRows struct {
	columns []string
	values  [][]driver.Value
	at      int
}

func (r *rowRaceRows) Columns() []string { return r.columns }
func (r *rowRaceRows) Close() error      { return nil }
func (r *rowRaceRows) Next(destination []driver.Value) error {
	if r.at >= len(r.values) {
		return io.EOF
	}
	copy(destination, r.values[r.at])
	r.at++
	return nil
}

var rowRaceDriverSequence uint64
var rowRaceDriverMu sync.Mutex

type rowRaceConnector struct{ d *rowRaceDriver }

func (c *rowRaceConnector) Connect(context.Context) (driver.Conn, error) {
	return &rowRaceConn{d: c.d}, nil
}
func (c *rowRaceConnector) Driver() driver.Driver { return c.d }

func rowRaceTestDB(t *testing.T, d *rowRaceDriver) *sqlx.DB {
	t.Helper()
	rowRaceDriverMu.Lock()
	rowRaceDriverSequence++
	name := fmt.Sprintf("row-race-%d", rowRaceDriverSequence)
	rowRaceDriverMu.Unlock()
	sql.Register(name, d)
	db := sqlx.NewDb(sql.OpenDB(&rowRaceConnector{d: d}), name)
	t.Cleanup(func() { _ = db.Close() })
	return db
}

func rowRaceTestTransaction(t *testing.T, d *rowRaceDriver) *sqlx.Tx {
	t.Helper()
	tx, err := rowRaceTestDB(t, d).Beginx()
	if err != nil {
		t.Fatalf("begin: %v", err)
	}
	t.Cleanup(func() { _ = tx.Rollback() })
	return tx
}

func (d *rowRaceDriver) statements() (queries, execs []rowRaceCall) {
	d.mu.Lock()
	defer d.mu.Unlock()
	return append([]rowRaceCall(nil), d.queries...), append([]rowRaceCall(nil), d.execs...)
}
