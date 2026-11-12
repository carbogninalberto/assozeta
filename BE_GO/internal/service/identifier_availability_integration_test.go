//go:build identifierintegration

package service

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

type identifierQueries struct {
	count atomic.Int64
	mu    sync.Mutex
	sql   string
	args  []any
}

func (q *identifierQueries) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	if strings.HasPrefix(strings.ToLower(strings.TrimSpace(d.SQL)), "select") {
		q.count.Add(1)
		q.mu.Lock()
		q.sql = d.SQL
		q.args = append([]any(nil), d.Args...)
		q.mu.Unlock()
	}
	return ctx
}
func (*identifierQueries) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

func TestIdentifierPostgres(t *testing.T) {
	var fixture struct {
		Batches []struct {
			Identifier, First string
			Count             int
		}
	}
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_IDENTIFIER_FIXTURE"))
	if err != nil || json.Unmarshal(raw, &fixture) != nil || len(fixture.Batches) != 4 {
		t.Fatal("run accounts.py --only identifiers")
	}
	cfg, err := pgx.ParseConfig(os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal("invalid private database configuration")
	}
	queries := &identifierQueries{}
	cfg.Tracer = queries
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	db := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	db.SetMaxIdleConns(4)
	flow := NewIdentifierAvailability(repository.NewIdentifierAccounts(db))
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	for i, batch := range fixture.Batches {
		if batch.Count != []int{1, 16, 128, 512}[i] {
			t.Fatal("incomplete query-count fixture")
		}
		queries.count.Store(0)
		result, err := flow.CheckEmail(ctx, batch.Identifier, true)
		if err != nil || result.Available || result.User == nil || result.User.ID != batch.First || queries.count.Load() != 1 {
			t.Fatal("growing duplicate set changed first selection or bounded query count", err)
		}
	}
	plans := map[string]json.RawMessage{}
	explain := func(label string) {
		t.Helper()
		queries.mu.Lock()
		sql, args := queries.sql, append([]any(nil), queries.args...)
		queries.mu.Unlock()
		var raw []byte
		if err := db.GetContext(ctx, &raw, "EXPLAIN (ANALYZE, FORMAT JSON) "+sql, args...); err != nil {
			t.Fatal("identifier query plan unavailable", err)
		}
		var value []struct {
			Plan struct {
				Rows int `json:"Actual Rows"`
			}
		}
		if json.Unmarshal(raw, &value) != nil || len(value) != 1 || value[0].Plan.Rows != 1 {
			t.Fatal("identifier query did not bound returned rows")
		}
		plans[label] = raw
	}
	explain("email_first_512_duplicates")
	queries.count.Store(0)
	result, err := flow.CheckEmail(ctx, "availability-fallback", true)
	if err != nil || result.User == nil || result.User.ID != "00000000-0000-4000-8000-000000080006" || queries.count.Load() != 2 {
		t.Fatal("username fallback query/order differs", err)
	}
	queries.count.Store(0)
	result, err = flow.CheckUsername(ctx, "availability-mixed")
	if err != nil || result.Available || result.User != nil || queries.count.Load() != 1 {
		t.Fatal("username existence lookup differs", err)
	}
	explain("username_exists")
	queries.count.Store(0)
	result, err = flow.CheckEmail(ctx, "availability-unused", true)
	if err != nil || !result.Available || result.User != nil || queries.count.Load() != 2 {
		t.Fatal("absent identifier became a row or unbounded lookup", err)
	}
	queries.count.Store(0)
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			result, err := flow.CheckEmail(ctx, fixture.Batches[3].Identifier, true)
			if err != nil || result.User == nil || result.User.ID != fixture.Batches[3].First {
				t.Error("concurrent identifier selection differs", err)
			}
		}()
	}
	wg.Wait()
	if queries.count.Load() != 32 || db.Stats().InUse != 0 || db.Stats().OpenConnections > 4 {
		t.Fatal("identifier concurrency exceeded query/pool budget")
	}
	cancelled, stop := context.WithCancel(ctx)
	stop()
	before := queries.count.Load()
	if _, err := flow.CheckUsername(cancelled, "name"); !errors.Is(err, context.Canceled) || queries.count.Load() != before {
		t.Fatal("cancelled identifier request reached database")
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := flow.CheckUsername(ctx, "name"); err == nil {
		t.Fatal("closed database reported available username")
	}
	encoded, err := json.MarshalIndent(plans, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(os.Getenv("ASSOZETA_IDENTIFIER_PLAN"), append(encoded, '\n'), 0600); err != nil {
		t.Fatal("cannot save private query-plan evidence", err)
	}
}
