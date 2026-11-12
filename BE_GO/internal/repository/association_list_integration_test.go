//go:build associationlistintegration

package repository

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
)

type associationListCounter struct{ queries atomic.Int64 }

func (c *associationListCounter) TraceQueryStart(ctx context.Context, _ *pgx.Conn, _ pgx.TraceQueryStartData) context.Context {
	c.queries.Add(1)
	return ctx
}
func (c *associationListCounter) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

// The parent adapter owns the migrated database and ORM-seeded rows; it grows
// the association count between the two phases named by the environment.
func TestAssociationListQueryCountLive(t *testing.T) {
	cfg, err := pgx.ParseConfig(os.Getenv("ASSOZETA_ASSOCIATION_LIST_DSN"))
	if err != nil || cfg.Database == "" || cfg.User != "contracts" {
		t.Fatal("private parent-owned database required")
	}
	counter := &associationListCounter{}
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	cfg.Tracer = counter
	db := sqlx.NewDb(database.OpenDB(*cfg), "pgx")
	defer db.Close()
	var want struct {
		Rows int `json:"rows"`
	}
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_ASSOCIATION_LIST_FIXTURE"))
	if err != nil || json.Unmarshal(raw, &want) != nil || want.Rows == 0 {
		t.Fatal("parent fixture required", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	repo := NewAssociationList(db)
	if _, err = repo.AssociationList(ctx, nil); err != nil { // warm the pool
		t.Fatal(err)
	}
	before := counter.queries.Load()
	started := time.Now()
	rows, err := repo.AssociationList(ctx, nil)
	elapsed := time.Since(started)
	if err != nil {
		t.Fatal(err)
	}
	queries := counter.queries.Load() - before
	if len(rows) != want.Rows || queries != 1 {
		t.Fatalf("rows=%d want %d queries=%d", len(rows), want.Rows, queries)
	}
	for _, row := range rows {
		if row.OnboardingCount != 1 || row.Onboarding == nil || !row.BillingPresent || row.NotesCount != 1 {
			t.Fatalf("row %s lost joined data", row.AssociationID)
		}
	}
	receipt, _ := json.Marshal(map[string]any{"rows": len(rows), "queries": queries, "elapsed_ms": elapsed.Milliseconds()})
	if err = os.WriteFile(filepath.Join(os.Getenv("ASSOZETA_ASSOCIATION_LIST_RECEIPTS"), "go.json"), receipt, 0o600); err != nil {
		t.Fatal(err)
	}
}
