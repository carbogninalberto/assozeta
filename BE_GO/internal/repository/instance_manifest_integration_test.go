//go:build instancebootstrapintegration

package repository

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

func TestInstanceManifestPostgresCancellation(t *testing.T) {
	cfg, err := pgx.ParseConfig(os.Getenv("ASSOZETA_INSTANCE_BOOTSTRAP_DSN"))
	if err != nil || cfg.Database != "assozeta_contract_bootstrap" || cfg.User != "contracts" || !strings.HasPrefix(filepath.Base(cfg.Host), "az-bootstrap-") {
		t.Fatal("private current-Django fixture required")
	}
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	db := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(3)
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	blocker, err := db.BeginTxx(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer blocker.Rollback()
	var blockerPID int
	if err := blocker.GetContext(ctx, &blockerPID, "SELECT pg_backend_pid()"); err != nil {
		t.Fatal(err)
	}
	if _, err := blocker.ExecContext(ctx, "LOCK TABLE instance_instanceconfiguration IN ACCESS EXCLUSIVE MODE"); err != nil {
		t.Fatal(err)
	}
	readCtx, stopRead := context.WithCancel(ctx)
	defer stopRead()
	done := make(chan error, 1)
	go func() {
		_, err := NewInstanceManifest(db).FirstManifestBranding(readCtx)
		done <- err
	}()
	var readerPID int
	for {
		err = db.GetContext(ctx, &readerPID, `SELECT COALESCE(min(pid),0) FROM pg_stat_activity
 WHERE datname=current_database() AND query=$1 AND state='active' AND wait_event_type='Lock'
 AND $2=ANY(pg_blocking_pids(pid))`, instanceManifestSQL, blockerPID)
		if err != nil {
			t.Fatal(err)
		}
		if readerPID != 0 {
			break
		}
		select {
		case err := <-done:
			t.Fatal("manifest read ended before observed lock wait", err)
		case <-ctx.Done():
			t.Fatal("manifest lock wait was not observed", ctx.Err())
		case <-time.After(10 * time.Millisecond):
		}
	}
	stopRead()
	select {
	case err := <-done:
		if !errors.Is(err, context.Canceled) {
			t.Fatal("blocked read did not return cancellation", err)
		}
	case <-ctx.Done():
		t.Fatal("blocked read did not return before blocker release")
	}
	for {
		var released bool
		if err := db.GetContext(ctx, &released, `SELECT NOT EXISTS(SELECT 1 FROM pg_stat_activity
 WHERE pid=$1 AND (state IS DISTINCT FROM 'idle' OR xact_start IS NOT NULL OR wait_event_type='Lock'))`, readerPID); err != nil {
			t.Fatal(err)
		}
		if released && db.Stats().InUse == 1 { // Only the still-held blocker.
			break
		}
		select {
		case <-ctx.Done():
			t.Fatal("reader backend or pool remained occupied before blocker release")
		case <-time.After(10 * time.Millisecond):
		}
	}
	if err := blocker.Rollback(); err != nil {
		t.Fatal(err)
	}
	value, err := NewInstanceManifest(db).FirstManifestBranding(ctx)
	if err != nil || value == nil || value.Name != "Fixture à ASD" || db.Stats().InUse != 0 {
		t.Fatal("manifest recovery or pool reuse failed", value, err, db.Stats())
	}
}
