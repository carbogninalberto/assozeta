// Private native probe: actual repository transaction, ordinary deferred SQL
// trigger, and failed cancellation transport. No production endpoint is exposed.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"sync/atomic"

	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
)

func event(name string) { _ = json.NewEncoder(os.Stdout).Encode(map[string]string{"event": name}) }
func run() error {
	cfg, err := pgx.ParseConfig(os.Getenv("ASSOZETA_DRAIN_WRITER_DSN"))
	if err != nil {
		return errors.New("invalid private database configuration")
	}
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	cfg.RuntimeParams["application_name"] = "az-restore-fence-writer"
	var calls atomic.Int64
	dial := (&net.Dialer{}).DialContext
	cfg.DialFunc = func(ctx context.Context, network, address string) (net.Conn, error) {
		if calls.Add(1) > 1 {
			return nil, errors.New("private cancellation transport unavailable")
		}
		return dial(ctx, network, address)
	}
	db := sqlx.NewDb(ownedpgx.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	lease, err := repository.NewRestoreFileLock(os.Getenv("ASSOZETA_DRAIN_LOCK_PATH")).Acquire(ctx, false, true)
	if err != nil {
		return errors.New("private lease acquisition failed")
	}
	defer lease.Close()
	tx, err := repository.NewProfileTables(db).BeginProfileTables(ctx)
	if err != nil {
		return errors.New("private transaction begin failed")
	}
	defer tx.Rollback()
	const owner = "00000000-0000-4000-8000-000000000001"
	if _, err := tx.TablePreferencesForUpdate(ctx, owner); err != nil {
		return errors.New("private row lock failed")
	}
	if err := tx.SetTablePreferences(ctx, owner, json.RawMessage(`{"late_commit":true}`)); err != nil {
		return errors.New("private update failed")
	}
	event("prepared")
	input := bufio.NewScanner(os.Stdin)
	if !input.Scan() || input.Text() != "commit" {
		return errors.New("missing private commit command")
	}
	done := make(chan error, 1)
	go func() { done <- tx.Commit() }()
	event("commit_started")
	if !input.Scan() || input.Text() != "cancel" {
		return errors.New("missing private cancellation command")
	}
	cancel()
	if err := <-done; err == nil {
		return errors.New("cancelled commit unexpectedly acknowledged")
	}
	// Completion joins local driver cleanup. The server can still be processing
	// its ordinary deferred trigger because the separate cancel transport failed.
	_ = lease.Close()
	event("local_cleanup")
	return nil
}
func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err.Error())
		os.Exit(1)
	}
}
