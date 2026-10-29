//go:build profiletablesintegration

package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
)

type tablePreferenceQueries struct{ reads, writes, inserts atomic.Int64 }

func (q *tablePreferenceQueries) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	query := strings.ToLower(strings.TrimSpace(d.SQL))
	switch {
	case strings.HasPrefix(query, "select"):
		q.reads.Add(1)
	case strings.HasPrefix(query, "update"):
		q.writes.Add(1)
	case strings.HasPrefix(query, "insert"):
		q.inserts.Add(1)
	}
	return ctx
}
func (*tablePreferenceQueries) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}
func (q *tablePreferenceQueries) reset()                                                        { q.reads.Store(0); q.writes.Store(0); q.inserts.Store(0) }
func (q *tablePreferenceQueries) expect(t *testing.T, r, w, i int64) {
	t.Helper()
	if q.reads.Load() != r || q.writes.Load() != w || q.inserts.Load() != i {
		t.Fatalf("SELECT/UPDATE/INSERT = %d/%d/%d; want %d/%d/%d", q.reads.Load(), q.writes.Load(), q.inserts.Load(), r, w, i)
	}
}

func TestProfileTablesPostgres(t *testing.T) {
	dsn := os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN")
	if dsn == "" {
		t.Fatal("run accounts.py --only profile-tables")
	}
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	q := &tablePreferenceQueries{}
	cfg.Tracer = q
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	db := sqlx.NewDb(ownedpgx.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	db.SetMaxIdleConns(4)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	const owner = "00000000-0000-4000-8000-000000120003"
	var before json.RawMessage
	var email string
	var lastAudit int64
	if err = db.GetContext(ctx, &before, `SELECT tables_settings FROM bakney_user WHERE user_id=$1`, owner); err != nil {
		t.Fatal(err)
	}
	if err = db.GetContext(ctx, &email, `SELECT email FROM bakney_user WHERE user_id=$1`, owner); err != nil {
		t.Fatal(err)
	}
	if err = db.GetContext(ctx, &lastAudit, `SELECT COALESCE(MAX(id),0) FROM auditlog_logentry`); err != nil {
		t.Fatal(err)
	}
	restore := func() {
		cleanup, stop := context.WithTimeout(context.Background(), 10*time.Second)
		defer stop()
		if _, err := db.ExecContext(cleanup, `DELETE FROM application_auditlogindex WHERE log_entry_id IN (SELECT id FROM auditlog_logentry WHERE id>$1 AND object_pk=$2)`, lastAudit, owner); err != nil {
			t.Fatal(err)
		}
		if _, err := db.ExecContext(cleanup, `DELETE FROM auditlog_logentry WHERE id>$1 AND object_pk=$2`, lastAudit, owner); err != nil {
			t.Fatal(err)
		}
		if _, err := db.ExecContext(cleanup, `UPDATE bakney_user SET tables_settings=$2::jsonb WHERE user_id=$1`, owner, string(before)); err != nil {
			t.Fatal(err)
		}
	}
	defer restore()
	person := &domain.Account{ID: owner, Role: 1, Email: email}
	identity := domain.ResolvedIdentity{Actor: person, Effective: person, ScopeUser: person}
	flow := NewProfileTables(repository.NewProfileTables(db), time.Now)
	input := dto.ProfileTablesRequest{Present: true, Value: domain.RequestObject{{Name: "columns", Value: []any{"name", true}}}}
	q.reset()
	if err = flow.Update(ctx, identity, dto.ProfileTablesRequest{}, domain.LoginAuditContext{}); err != nil {
		t.Fatal(err)
	}
	q.expect(t, 0, 0, 0)
	for _, size := range []int{1, 16, 128, 512} {
		restore()
		values := domain.RequestObject{}
		for n := 0; n < size; n++ {
			values = append(values, domain.RequestField{Name: fmt.Sprintf("table-%04d", n), Value: []any{"name", false, json.Number("9007199254740993")}})
		}
		q.reset()
		if err = flow.Update(ctx, identity, dto.ProfileTablesRequest{Present: true, Value: values}, domain.LoginAuditContext{}); err != nil {
			t.Fatal(size, err)
		}
		q.expect(t, 3, 1, 2)
		q.reset()
		if err = flow.Update(ctx, identity, dto.ProfileTablesRequest{Present: true, Value: values}, domain.LoginAuditContext{}); err != nil {
			t.Fatal(size, err)
		}
		q.expect(t, 2, 1, 0)
	}
	restore()
	q.reset()
	var wg sync.WaitGroup
	for i := 0; i < 24; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if err := flow.Update(ctx, identity, input, domain.LoginAuditContext{}); err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
	q.expect(t, 49, 24, 2)
	var events int
	if err = db.GetContext(ctx, &events, `SELECT count(*) FROM auditlog_logentry WHERE id>$1`, lastAudit); err != nil || events != 1 {
		t.Fatal("duplicate audit", events, err)
	}
	for _, stage := range []string{"row", "audit"} {
		restore()
		blocker, err := db.BeginTxx(ctx, nil)
		if err != nil {
			t.Fatal(err)
		}
		if stage == "row" {
			var id string
			err = blocker.GetContext(ctx, &id, `SELECT user_id FROM bakney_user WHERE user_id=$1 FOR UPDATE`, owner)
		} else {
			_, err = blocker.ExecContext(ctx, `LOCK TABLE auditlog_logentry IN SHARE MODE`)
		}
		if err != nil {
			blocker.Rollback()
			t.Fatal(err)
		}
		q.reset()
		blocked, stop := context.WithTimeout(ctx, 100*time.Millisecond)
		err = flow.Update(blocked, identity, input, domain.LoginAuditContext{})
		stop()
		blocker.Rollback()
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatal(stage, "did not cancel", err)
		}
		if stage == "row" {
			q.expect(t, 2, 0, 0)
		} else {
			q.expect(t, 3, 1, 1)
		}
		if db.Stats().InUse != 0 {
			t.Fatal("cancelled write retained connection")
		}
		var after json.RawMessage
		if err = db.GetContext(ctx, &after, `SELECT tables_settings FROM bakney_user WHERE user_id=$1`, owner); err != nil || string(after) != string(before) {
			t.Fatal("cancelled write persisted preferences", err)
		}
		if err = db.GetContext(ctx, &events, `SELECT count(*) FROM auditlog_logentry WHERE id>$1`, lastAudit); err != nil || events != 0 {
			t.Fatal("cancelled write left audit", events, err)
		}
		if err = flow.Update(ctx, identity, input, domain.LoginAuditContext{}); err != nil {
			t.Fatal("recovery", err)
		}
	}
	restore()
	tx, err := repository.NewProfileTables(db).BeginProfileTables(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if _, err = tx.TablePreferencesForUpdate(ctx, "00000000-0000-4000-8000-000000000004"); !errors.Is(err, domain.ErrAccountNotFound) {
		tx.Rollback()
		t.Fatal("deleted account admitted", err)
	}
	if err = tx.SetTablePreferences(ctx, owner, json.RawMessage("invalid")); !errors.Is(err, domain.ErrInvalidScopedRequest) {
		tx.Rollback()
		t.Fatal("invalid JSON admitted", err)
	}
	tx.Rollback()
	if db.Stats().InUse != 0 || db.Stats().OpenConnections > 4 {
		t.Fatal("pool not released or bounded")
	}
	t.Log("1/16/128/512 table entries retain 2 SELECT/1 UPDATE/2 INSERT; identical repetition 1/1/0; missing field 0/0/0; 24 competitors 25/24/2 with one audit. Pre-write/mid-audit cancellation rolls back and recovers within four connections.")
}
