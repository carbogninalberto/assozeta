package repository

import (
	"context"
	"database/sql"
	"errors"
	"sync"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/jmoiron/sqlx"
)

// ownedTransaction retains its connection until transaction completion and any
// cancelled-driver cleanup have finished. database/sql's ErrTxDone alone does
// not provide that guarantee: automatic rollback sets done before calling the
// driver. A surrounding restore lease must outlive that work.
//
// The database must use database.OpenDB or its owned connector. Pool-backed
// stdlib connections cannot provide this physical-cleanup guarantee.
type ownedTransaction struct {
	tx         *sqlx.Tx
	connection *sqlx.Conn
	completion *database.Completion
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
		if !database.SupportsOwnedTransactions(raw) {
			return errors.New("transaction requires the owned pgx connector")
		}
		return nil
	})
	if err != nil {
		_ = connection.Close()
		return nil, err
	}
	// Keep the original context: cancellation must still abort queries and
	// prevent a subsequent commit. Completion joins its automatic rollback.
	transactionContext, completion := database.TrackTransaction(ctx)
	owned.completion = completion
	owned.tx, err = connection.BeginTxx(transactionContext, options)
	if err != nil {
		_ = owned.finish()
		return nil, err
	}
	// This separate acknowledged command precedes every effect statement.
	// A cancelled waiter cannot send later writes after a restore's exclusive
	// server barrier passes. A CTE inside the mutation would not establish that
	// protocol ordering. This SELECT also establishes REPEATABLE READ snapshots.
	_, err = owned.tx.ExecContext(ctx, `SELECT pg_advisory_xact_lock_shared($1::int,$2::int)`, 1095981639, 1381192786)
	if err != nil {
		_ = owned.Rollback()
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
	finishErr := t.finish()
	if err != nil {
		return err
	}
	return finishErr
}

func (t *ownedTransaction) Rollback() error {
	err := t.tx.Rollback()
	finishErr := t.finish()
	if err != nil {
		return err
	}
	return finishErr
}

func (t *ownedTransaction) finish() error {
	t.finished.Do(func() {
		if t.completion.Started() {
			// ErrTxDone can precede the driver's terminal call. Its notification
			// also distinguishes a concurrent successful completion from failure.
			result := t.completion.Wait()
			if result.Discarded {
				<-result.Cleanup
			}
			t.finishErr = result.CloseErr
		}
		// Never call Raw after BeginTxx: Go 1.25 Conn.grabConn can race its
		// internal close and return a nil driver connection. Close itself is
		// safe, including when automatic rollback already closed this sql.Conn.
		if err := t.connection.Close(); err != nil && !errors.Is(err, sql.ErrConnDone) && t.finishErr == nil {
			t.finishErr = err
		}
	})
	return t.finishErr
}
