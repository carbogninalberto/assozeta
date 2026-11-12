//go:build instancebootstrapintegration

package repository

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/database"
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

// Concurrent public reads share a bounded production-owned pool. After every
// pooled backend is terminated server-side, pgx's idle-session ping (idle > 1s)
// must discard each stale connection so no request observes the termination.
func TestInstanceManifestPostgresConcurrentPoolRecovery(t *testing.T) {
	cfg, err := pgx.ParseConfig(os.Getenv("ASSOZETA_INSTANCE_BOOTSTRAP_DSN"))
	if err != nil || cfg.Database != "assozeta_contract_bootstrap" || cfg.User != "contracts" || !strings.HasPrefix(filepath.Base(cfg.Host), "az-bootstrap-") {
		t.Fatal("private current-Django fixture required")
	}
	adminCfg := cfg.Copy()
	application := "az-manifest-pool-" + strconv.FormatInt(time.Now().UnixNano(), 36)
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	cfg.RuntimeParams["application_name"] = application
	db := sqlx.NewDb(database.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	db.SetMaxIdleConns(4)
	admin := sqlx.NewDb(stdlib.OpenDB(*adminCfg), "pgx")
	defer admin.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	repository := NewInstanceManifest(db)
	wave := func() (int, error) {
		start := make(chan struct{})
		failures := make(chan error, 32*4)
		var group sync.WaitGroup
		for worker := 0; worker < 32; worker++ {
			group.Add(1)
			go func() {
				defer group.Done()
				<-start
				for read := 0; read < 4; read++ {
					value, err := repository.FirstManifestBranding(ctx)
					if err == nil && (value == nil || value.Name != "Fixture à ASD") {
						err = errors.New("unexpected manifest branding")
					}
					if err != nil {
						failures <- err
					}
				}
			}()
		}
		close(start)
		group.Wait()
		close(failures)
		count := 0
		var first error
		for err := range failures {
			if first == nil {
				first = err
			}
			count++
		}
		return count, first
	}
	backends := func() []int {
		t.Helper()
		var pids []int
		if err := admin.SelectContext(ctx, &pids, `SELECT pid FROM pg_stat_activity
 WHERE datname=current_database() AND application_name=$1 ORDER BY pid`, application); err != nil {
			t.Fatal(err)
		}
		return pids
	}
	if failed, first := wave(); failed != 0 {
		t.Fatal("concurrent manifest reads failed", failed, first)
	}
	stats := db.Stats()
	if stats.InUse != 0 || stats.OpenConnections > 4 || stats.WaitCount == 0 {
		t.Fatal("pool was not bounded, contended and released", stats)
	}
	terminated := backends()
	if len(terminated) == 0 || len(terminated) > 4 || len(terminated) != stats.OpenConnections {
		t.Fatal("server backends do not match the bounded pool", terminated, stats)
	}
	var idle bool
	if err := admin.GetContext(ctx, &idle, `SELECT bool_and(state='idle' AND xact_start IS NULL) FROM pg_stat_activity
 WHERE application_name=$1`, application); err != nil || !idle {
		t.Fatal("pooled backends retained work or transactions", idle, err)
	}
	var killed int
	if err := admin.GetContext(ctx, &killed, `SELECT count(*) FILTER (WHERE pg_terminate_backend(pid)) FROM pg_stat_activity
 WHERE datname=current_database() AND application_name=$1`, application); err != nil || killed != len(terminated) {
		t.Fatal("pool backends were not terminated", killed, err)
	}
	for len(backends()) != 0 {
		select {
		case <-ctx.Done():
			t.Fatal("terminated backends remained", ctx.Err())
		case <-time.After(10 * time.Millisecond):
		}
	}
	// Exceed pgx's default reset ping threshold; sub-second reuse is outside
	// this test and still reports a stale connection to its caller.
	time.Sleep(1100 * time.Millisecond)
	if failed, first := wave(); failed != 0 {
		t.Fatal("reads observed terminated pool connections", failed, first)
	}
	recovered := backends()
	for _, pid := range recovered {
		for _, old := range terminated {
			if pid == old {
				t.Fatal("terminated backend reused", pid)
			}
		}
	}
	if stats := db.Stats(); stats.InUse != 0 || stats.OpenConnections > 4 || len(recovered) == 0 || len(recovered) != stats.OpenConnections {
		t.Fatal("pool did not recover within its bound", stats, recovered)
	}
}
