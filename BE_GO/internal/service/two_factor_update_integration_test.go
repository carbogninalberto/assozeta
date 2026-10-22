//go:build twofactorupdateintegration

package service

import (
	"context"
	"errors"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

type twoFactorWriteQueries struct{ count atomic.Int64 }

func (q *twoFactorWriteQueries) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	sql := strings.ToLower(strings.TrimSpace(d.SQL))
	if strings.HasPrefix(sql, "select") || strings.HasPrefix(sql, "update") || strings.HasPrefix(sql, "insert") {
		q.count.Add(1)
	}
	return ctx
}
func (*twoFactorWriteQueries) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

func TestTwoFactorUpdatePostgres(t *testing.T) {
	dsn := os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN")
	if dsn == "" {
		t.Fatal("run accounts.py --only two-factor-update")
	}
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	queries := &twoFactorWriteQueries{}
	cfg.Tracer = queries
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	db := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	db.SetMaxIdleConns(4)
	writer, err := sqlx.Connect("pgx", dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer writer.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	id := "00000000-0000-4000-8000-000000081200"
	var enabled bool
	if err = writer.Get(&enabled, "SELECT two_fa FROM bakney_user WHERE user_id=$1", id); err != nil || !enabled {
		t.Fatal("private fixture missing", err)
	}
	reset := func() {
		if _, err := writer.Exec("UPDATE bakney_user SET two_fa=TRUE WHERE user_id=$1", id); err != nil {
			t.Error(err)
		}
		if _, err := writer.Exec("DELETE FROM auditlog_logentry WHERE cid='two-factor-query-test'"); err != nil {
			t.Error(err)
		}
	}
	defer reset()
	account := &domain.Account{ID: id, Email: "two-fa-update-0@example.test"}
	identity := domain.ResolvedIdentity{Actor: account, Effective: account, ScopeUser: account}
	cid := "two-factor-query-test"
	flow := NewTwoFactorUpdates(repository.NewTwoFactorUpdates(db), time.Now)
	update := func(ctx context.Context) error {
		_, err := flow.Update(ctx, identity, dto.TwoFactorUpdateRequest{}, domain.LoginAuditContext{CorrelationID: &cid})
		return err
	}
	for _, calls := range []int{1, 32} {
		reset()
		queries.count.Store(0)
		var wg sync.WaitGroup
		for n := 0; n < calls; n++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				if err := update(ctx); err != nil {
					t.Error(err)
				}
			}()
		}
		wg.Wait()
		// One SELECT FOR UPDATE and one UPDATE per request. Only the actual
		// transition adds the batched association read and audit INSERT SELECT.
		if queries.count.Load() != int64(2*calls+2) {
			t.Fatalf("write query budget: %d for %d calls", queries.count.Load(), calls)
		}
		var count int
		if err = writer.Get(&count, "SELECT count(*) FROM auditlog_logentry WHERE cid=$1", cid); err != nil || count != 1 {
			t.Fatal("concurrent duplicate audit", err, count)
		}
		if db.Stats().InUse != 0 || db.Stats().OpenConnections > 4 {
			t.Fatal("connection budget exceeded")
		}
	}
	reset()
	lock, err := writer.BeginTxx(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Rollback()
	if _, err = lock.ExecContext(ctx, "SELECT user_id FROM bakney_user WHERE user_id=$1 FOR UPDATE", id); err != nil {
		t.Fatal(err)
	}
	blockedCtx, stop := context.WithTimeout(ctx, 150*time.Millisecond)
	err = update(blockedCtx)
	stop()
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("locked mutation did not respect cancellation", err)
	}
	if err = lock.Rollback(); err != nil {
		t.Fatal(err)
	}
	if err = writer.Get(&enabled, "SELECT two_fa FROM bakney_user WHERE user_id=$1", id); err != nil || !enabled {
		t.Fatal("cancelled mutation changed user", err)
	}
	var count int
	if err = writer.Get(&count, "SELECT count(*) FROM auditlog_logentry WHERE cid=$1", cid); err != nil || count != 0 {
		t.Fatal("cancelled mutation left audit", err)
	}
	if err = update(ctx); err != nil {
		t.Fatal("pool did not recover after cancellation", err)
	}
}
