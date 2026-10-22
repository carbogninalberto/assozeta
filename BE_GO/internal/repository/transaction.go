package repository

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"errors"
	"sync"

	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
)

// ownedTransaction retains its connection until transaction completion and any
// cancelled-driver cleanup have finished. database/sql's ErrTxDone alone does
// not provide that guarantee: automatic rollback sets done before calling the
// driver. A surrounding restore lease must outlive that work.
//
// The database must own its pgx connections, as with stdlib.OpenDB or sql.Open
// using the pgx driver. stdlib.OpenDBFromPool is not supported: discarding that
// wrapper returns a connection to another pool instead of closing its socket.
type ownedTransaction struct {
	tx         *sqlx.Tx
	connection *sqlx.Conn
	cleanup    <-chan struct{}
	finished   sync.Once
	finishErr  error
}

func beginOwnedTransaction(ctx context.Context, db *sqlx.DB, options *sql.TxOptions) (*ownedTransaction, error) {
	connection, err := db.Connx(ctx)
	if err != nil {
		return nil, err
	}
	owned := &ownedTransaction{connection: connection}
	err = connection.Raw(func(raw any) error {
		// Capture only the immutable cleanup channel while the driver connection
		// is locked. Never inspect a pgx pointer after returning it to the pool.
		provider, ok := raw.(interface{ Conn() *pgx.Conn })
		if !ok || provider.Conn() == nil {
			return errors.New("transaction requires an owned pgx connection")
		}
		owned.cleanup = provider.Conn().PgConn().CleanupDone()
		return nil
	})
	if err != nil {
		_ = connection.Close()
		return nil, err
	}
	// Keep the original context: cancellation must still abort queries and
	// prevent a subsequent commit. Completion joins its automatic rollback.
	owned.tx, err = connection.BeginTxx(ctx, options)
	if err != nil {
		_ = owned.finish(err)
		return nil, err
	}
	return owned, nil
}

func (t *ownedTransaction) Commit() error {
	err := t.tx.Commit()
	if err != nil {
		// Commit can reject a cancelled context before automatic rollback has
		// acquired the transaction. Ensure it is ending before discarding.
		_ = t.tx.Rollback()
	}
	finishErr := t.finish(err)
	if err != nil {
		return err
	}
	return finishErr
}

func (t *ownedTransaction) Rollback() error {
	err := t.tx.Rollback()
	finishErr := t.finish(err)
	if err != nil {
		return err
	}
	return finishErr
}

func (t *ownedTransaction) finish(transactionErr error) error {
	t.finished.Do(func() {
		if transactionErr == nil {
			// A synchronous successful COMMIT/ROLLBACK is acknowledged by the
			// server. Preserve normal pool reuse without waiting for a live
			// connection's cleanup channel, which is intentionally still open.
			t.finishErr = t.connection.Close()
			return
		}
		// Returning ErrBadConn from Raw explicitly discards the private
		// connection. Raw releases the driver mutex before closing, so close
		// can wait for the transaction's connection lease without deadlocking
		// automatic rollback. If automatic error cleanup already closed the
		// sql.Conn, its captured pgx cleanup channel still joins that close.
		_ = t.connection.Raw(func(any) error { return driver.ErrBadConn })
		<-t.cleanup
	})
	return t.finishErr
}
