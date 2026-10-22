//go:build ownedtransactionintegration

package repository

import (
	"context"
	"database/sql"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/testutil/transactionbarrier"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

func TestOwnedTransactionPostgres(t *testing.T) {
	dsn := os.Getenv("ASSOZETA_OWNED_TRANSACTION_DSN")
	cfg, err := pgx.ParseConfig(dsn)
	if dsn == "" || err != nil || cfg.Database != "assozeta_contract_owned_tx" || cfg.User != "contracts" || !strings.HasPrefix(filepath.Base(cfg.Host), "az-tx-lifetime-") {
		t.Fatal("required private owned-stdlib PostgreSQL fixture")
	}
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	admin := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	defer admin.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	if _, err = admin.ExecContext(ctx, `CREATE TABLE owned_transaction_probe(id integer PRIMARY KEY,value integer NOT NULL); INSERT INTO owned_transaction_probe VALUES(1,0)`); err != nil {
		t.Fatal(err)
	}
	fresh := func(t *testing.T) (*sqlx.DB, *transactionbarrier.Control) {
		t.Helper()
		db, control, e := transactionbarrier.OpenConfig(cfg)
		if e != nil {
			t.Fatal(e)
		}
		db.SetMaxOpenConns(1)
		db.SetMaxIdleConns(1)
		t.Cleanup(func() { control.ReleaseAll(); _ = db.Close() })
		return db, control
	}
	waitGate := func(t *testing.T, g *transactionbarrier.Gate) {
		t.Helper()
		limit, stop := context.WithTimeout(ctx, 3*time.Second)
		defer stop()
		if e := g.Wait(limit); e != nil {
			t.Fatal("driver barrier was not reached", e)
		}
	}
	blocked := func(t *testing.T, done <-chan error) {
		t.Helper()
		select {
		case e := <-done:
			t.Fatal("caller returned before driver cleanup", e)
		case <-time.After(20 * time.Millisecond):
		}
	}
	await := func(t *testing.T, done <-chan error) error {
		t.Helper()
		select {
		case e := <-done:
			return e
		case <-time.After(3 * time.Second):
			t.Fatal("transaction completion did not join cleanup")
			return nil
		}
	}
	rowBusy := func(t *testing.T) {
		t.Helper()
		_, e := admin.ExecContext(ctx, `SELECT id FROM owned_transaction_probe WHERE id=1 FOR UPDATE NOWAIT`)
		var pg *pgconn.PgError
		if !errors.As(e, &pg) || pg.Code != "55P03" {
			t.Fatal("actual PostgreSQL row lock not held", e)
		}
	}
	rowReleased := func(t *testing.T) {
		t.Helper()
		deadline := time.Now().Add(2 * time.Second)
		for {
			_, e := admin.ExecContext(ctx, `SELECT id FROM owned_transaction_probe WHERE id=1 FOR UPDATE NOWAIT`)
			if e == nil {
				return
			}
			if time.Now().After(deadline) {
				t.Fatal("PostgreSQL did not release disconnected transaction", e)
			}
			time.Sleep(time.Millisecond)
		}
	}
	value := func(t *testing.T) int {
		t.Helper()
		var n int
		if e := admin.GetContext(ctx, &n, `SELECT value FROM owned_transaction_probe WHERE id=1`); e != nil {
			t.Fatal(e)
		}
		return n
	}
	pid := func(t *testing.T, db *sqlx.DB) int {
		t.Helper()
		var n int
		if e := db.GetContext(ctx, &n, `SELECT pg_backend_pid()`); e != nil {
			t.Fatal(e)
		}
		return n
	}

	t.Run("healthy commit and rollback reuse owned connection", func(t *testing.T) {
		db, _ := fresh(t)
		before := pid(t, db)
		tx, e := beginOwnedTransaction(ctx, db, nil)
		if e != nil {
			t.Fatal(e)
		}
		if _, e = tx.tx.ExecContext(ctx, `UPDATE owned_transaction_probe SET value=1 WHERE id=1`); e != nil {
			t.Fatal(e)
		}
		if e = tx.Commit(); e != nil {
			t.Fatal(e)
		}
		if e = tx.Rollback(); !errors.Is(e, sql.ErrTxDone) {
			t.Fatal("repeat finish semantics", e)
		}
		next, e := beginOwnedTransaction(ctx, db, nil)
		if e != nil {
			t.Fatal(e)
		}
		if _, e = next.tx.ExecContext(ctx, `UPDATE owned_transaction_probe SET value=2 WHERE id=1`); e != nil {
			t.Fatal(e)
		}
		if e = next.Rollback(); e != nil {
			t.Fatal(e)
		}
		if value(t) != 1 || pid(t, db) != before || db.Stats().InUse != 0 {
			t.Fatal("healthy completion lost pool reuse or changed rollback value")
		}
	})

	t.Run("concurrent repeated completion joins one driver operation", func(t *testing.T) {
		db, control := fresh(t)
		tx, e := beginOwnedTransaction(ctx, db, nil)
		if e != nil {
			t.Fatal(e)
		}
		if _, e = tx.tx.ExecContext(ctx, `UPDATE owned_transaction_probe SET value=8 WHERE id=1`); e != nil {
			t.Fatal(e)
		}
		gate := control.ArmRollback()
		first, second := make(chan error, 1), make(chan error, 1)
		go func() { first <- tx.Rollback() }()
		waitGate(t, gate)
		go func() { second <- tx.Rollback() }()
		blocked(t, first)
		blocked(t, second)
		rowBusy(t)
		gate.Release()
		a, b := await(t, first), await(t, second)
		if (a != nil && !errors.Is(a, sql.ErrTxDone)) || (b != nil && !errors.Is(b, sql.ErrTxDone)) {
			t.Fatal(a, b)
		}
		rowReleased(t)
		if value(t) != 1 || db.Stats().InUse != 0 {
			t.Fatal("repeat completion leaked ownership")
		}
	})

	t.Run("cancelled begin leaves pool and server usable", func(t *testing.T) {
		db, control := fresh(t)
		before := pid(t, db)
		gate, closeGate := control.ArmBegin(), control.ArmClose()
		work, stop := context.WithCancel(ctx)
		defer stop()
		done := make(chan error, 1)
		go func() {
			tx, e := beginOwnedTransaction(work, db, nil)
			if tx != nil {
				_ = tx.Rollback()
			}
			done <- e
		}()
		waitGate(t, gate)
		stop()
		gate.Release()
		waitGate(t, closeGate)
		blocked(t, done)
		closeGate.Release()
		if e := await(t, done); !errors.Is(e, context.Canceled) {
			t.Fatal("cancelled begin not rejected", e)
		}
		if pid(t, db) == before || db.Stats().InUse != 0 {
			t.Fatal("failed begin retained its driver connection")
		}
	})

	t.Run("automatic rollback joins driver and socket cleanup before lease release", func(t *testing.T) {
		db, control := fresh(t)
		work, stop := context.WithCancel(ctx)
		defer stop()
		tx, e := beginOwnedTransaction(work, db, nil)
		if e != nil {
			t.Fatal(e)
		}
		if _, e = tx.tx.ExecContext(work, `UPDATE owned_transaction_probe SET value=99 WHERE id=1`); e != nil {
			t.Fatal(e)
		}
		locks := NewRestoreFileLock(filepath.Join(t.TempDir(), "lifecycle.lock"))
		lease, e := locks.Acquire(ctx, false, true)
		if e != nil {
			t.Fatal(e)
		}
		defer lease.Close()
		rollback, closeGate := control.ArmRollback(), control.ArmClose()
		stop()
		waitGate(t, rollback)
		done := make(chan error, 1)
		go func() { e := tx.Rollback(); _ = lease.Close(); done <- e }()
		assertHeld := func(requireRowLock bool) {
			blocked(t, done)
			if requireRowLock {
				rowBusy(t)
			}
			exclusive, e := locks.Acquire(ctx, true, true)
			if exclusive != nil {
				_ = exclusive.Close()
			}
			if !errors.Is(e, domain.ErrRestoreBusy) {
				t.Fatal("exclusive lifecycle lease admitted before cleanup", e)
			}
		}
		assertHeld(true)
		rollback.Release()
		waitGate(t, closeGate)
		assertHeld(false)
		closeGate.Release()
		if e := await(t, done); !errors.Is(e, sql.ErrTxDone) && !errors.Is(e, context.Canceled) {
			t.Fatal("unexpected cancelled completion", e)
		}
		rowReleased(t)
		exclusive, e := locks.Acquire(ctx, true, true)
		if e != nil {
			t.Fatal(e)
		}
		_ = exclusive.Close()
		if value(t) != 1 {
			t.Fatal("cancelled effect committed")
		}
		_ = pid(t, db)
	})

	t.Run("failed SQL discards connection before return", func(t *testing.T) {
		db, control := fresh(t)
		before := pid(t, db)
		tx, e := beginOwnedTransaction(ctx, db, nil)
		if e != nil {
			t.Fatal(e)
		}
		if _, e = tx.tx.ExecContext(ctx, `UPDATE owned_transaction_probe SET value=17 WHERE id=1`); e != nil {
			t.Fatal(e)
		}
		if _, e = tx.tx.ExecContext(ctx, `SELECT missing_fixture_column FROM owned_transaction_probe`); e == nil {
			t.Fatal("SQL fault absent")
		}
		closeGate := control.ArmClose()
		done := make(chan error, 1)
		go func() { done <- tx.Commit() }()
		waitGate(t, closeGate)
		blocked(t, done)
		closeGate.Release()
		if e := await(t, done); e == nil {
			t.Fatal("failed transaction committed")
		}
		if value(t) != 1 || pid(t, db) == before || db.Stats().InUse != 0 {
			t.Fatal("failed SQL cleanup or discard incomplete")
		}
	})
}
