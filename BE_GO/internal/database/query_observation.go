package database

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
)

// DriverObserver observes request-context driver work outside QueryTracer. The
// returned context must preserve the parent's cancellation, deadline and values.
// Finish is called once with the unchanged driver error; probe decisions attest
// attempts only, not wire traffic. Implementations must be concurrency-safe.
type DriverObserver interface {
	BeginDriverOperation(ctx context.Context, kind string) (context.Context, func(error))
	ObserveResetProbe(ctx context.Context)
}

const (
	DriverOperationConnect = "connect"
	DriverOperationReset   = "reset"
	DriverOperationPing    = "ping"
)

// OpenObservedDB optionally adds driver lifecycle accounting to OpenDB. The
// caller separately sets config.Tracer for business query callbacks. A nil
// observer uses OpenDB unchanged. The owned connector and transaction behavior
// remain underneath the decorator, and all optional interfaces are preserved.
func OpenObservedDB(config pgx.ConnConfig, observer DriverObserver) *sql.DB {
	if observer == nil {
		return OpenDB(config)
	}
	return sql.OpenDB(&observedConnector{
		Connector: WrapConnector(stdlib.GetConnector(config, stdlib.OptionShouldPing(observedShouldPing(observer)))),
		observer:  observer,
	})
}

func observedShouldPing(observer DriverObserver) func(context.Context, stdlib.ShouldPingParams) bool {
	return func(ctx context.Context, params stdlib.ShouldPingParams) bool {
		// Preserve pgx v5.9.2's default exactly, including equality at one second.
		ping := params.IdleDuration > time.Second
		if ping {
			observer.ObserveResetProbe(ctx)
		}
		return ping
	}
}

type observedConnector struct {
	driver.Connector
	observer DriverObserver
}

func (c *observedConnector) Connect(ctx context.Context) (_ driver.Conn, err error) {
	ctx, finish := c.observer.BeginDriverOperation(ctx, DriverOperationConnect)
	defer func() { finish(err) }()
	raw, err := c.Connector.Connect(ctx)
	if err != nil {
		return nil, err
	}
	owned, ok := raw.(*ownedConn)
	if !ok || owned == nil {
		// Production always uses WrapConnector above. Reject an invalid internal
		// construction rather than expose an unowned connection.
		if !ok && raw != nil {
			_ = raw.Close()
		}
		return nil, errors.New("query observation requires an owned connection")
	}
	return &observedConn{ownedConn: owned, observer: c.observer}, nil
}

// Embedding the concrete owned connection preserves the ownedTransactions
// marker, Validator, all pgx optional interfaces and transaction completion.
type observedConn struct {
	*ownedConn
	observer DriverObserver
}

func (c *observedConn) ResetSession(ctx context.Context) (err error) {
	ctx, finish := c.observer.BeginDriverOperation(ctx, DriverOperationReset)
	defer func() { finish(err) }()
	return c.ownedConn.ResetSession(ctx)
}

func (c *observedConn) Ping(ctx context.Context) (err error) {
	ctx, finish := c.observer.BeginDriverOperation(ctx, DriverOperationPing)
	defer func() { finish(err) }()
	return c.ownedConn.Ping(ctx)
}

var _ driver.Connector = (*observedConnector)(nil)
var _ pgxDriver = (*observedConn)(nil)
var _ driver.Validator = (*observedConn)(nil)
