//go:build subscriptionarchiveintegration

package service

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"os"
	"sync/atomic"
	"testing"
	"time"

	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/migrations"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
)

type archiveQueries struct{ count atomic.Int64 }

func (q *archiveQueries) TraceQueryStart(ctx context.Context, _ *pgx.Conn, _ pgx.TraceQueryStartData) context.Context {
	q.count.Add(1)
	return ctx
}
func (q *archiveQueries) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

// TestSubscriptionAutoArchivePostgres runs on the seed accounts.py --only
// subscription-auto-archive restored after the actual Django task ran on it.
// The first group leaves the Go result for the adapter to compare with the
// Django state; the others must leave every business row untouched.
func TestSubscriptionAutoArchivePostgres(t *testing.T) {
	var fixture struct{ Now time.Time }
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_ARCHIVE_FIXTURE"))
	if err != nil || json.Unmarshal(raw, &fixture) != nil || fixture.Now.IsZero() {
		t.Fatal("run accounts.py --only subscription-auto-archive")
	}
	dsn := os.Getenv("ASSOZETA_ARCHIVE_DSN")
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	trace := &archiveQueries{}
	cfg.Tracer = trace
	db := sqlx.NewDb(ownedpgx.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	if err = migrations.Apply(dsn); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	// Trusted private fixture handoff; no production ownership switch is exposed.
	if _, err = db.ExecContext(ctx, `UPDATE assozeta_go_job_ownership SET runtime='go',generation=generation+1 WHERE singleton=1`); err != nil {
		t.Fatal(err)
	}
	queue := repository.NewJobs(db)
	clock := func() time.Time { return fixture.Now }
	worker := NewSubscriptionAutoArchive(repository.NewSubscriptionAutoArchives(db, time.Now), clock, slog.New(slog.NewTextHandler(io.Discard, nil)))
	claim := func() domain.Job {
		t.Helper()
		tx, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer tx.Rollback()
		if _, e = queue.Enqueue(ctx, tx, domain.JobIntent{ID: uuid.New(), Kind: domain.JobAutoArchiveSubscription, Version: 1,
			Key: uuid.NewString(), Payload: json.RawMessage(`{}`), MaxAttempts: 2}); e != nil {
			t.Fatal(e)
		}
		if e = tx.Commit(); e != nil {
			t.Fatal(e)
		}
		jobs, e := queue.Claim(ctx, []string{domain.JobAutoArchiveSubscription}, "fixture", 1, time.Minute)
		if e != nil || len(jobs) != 1 {
			t.Fatal("claim", e)
		}
		return jobs[0]
	}
	state := func() string {
		t.Helper()
		var text string
		if e := db.GetContext(ctx, &text, `SELECT concat_ws('|',
 (SELECT string_agg(to_jsonb(s)::text,',' ORDER BY subscription_id) FROM application_subscription s),
 (SELECT string_agg(to_jsonb(p)::text,',' ORDER BY payment_id) FROM application_payment p),
 (SELECT string_agg(to_jsonb(i)::text,',' ORDER BY invoice_id) FROM application_invoice i),
 (SELECT count(*)::text FROM auditlog_logentry),(SELECT count(*)::text FROM application_auditlogindex))`); e != nil {
			t.Fatal(e)
		}
		return text
	}
	seeded := state()
	stored := func(j domain.Job) domain.Job {
		t.Helper()
		current, e := queue.Get(ctx, uuid.Nil, j.ID)
		if e != nil {
			t.Fatal(e)
		}
		return current
	}

	t.Run("invalid envelope before SQL", func(t *testing.T) {
		job := claim()
		cases := []domain.Job{job, job, job, job, job, job, job, job}
		cases[0].AssociationID = uuid.New()
		cases[1].ID = uuid.Nil
		cases[2].LeaseToken = uuid.Nil
		cases[3].Kind = "other"
		cases[4].Version = 2
		cases[5].State = "pending"
		cases[6].Payload = json.RawMessage(`null`)
		cases[7].Payload = json.RawMessage(`{"now":"2099-01-01"}`)
		for _, j := range cases {
			trace.count.Store(0)
			if e := worker.Run(ctx, j); !errors.Is(e, domain.ErrJobInvalid) || trace.count.Load() != 0 {
				t.Fatal("invalid envelope", e)
			}
		}
		if state() != seeded {
			t.Fatal("invalid envelope changed business rows")
		}
	})
	t.Run("cancelled context before SQL", func(t *testing.T) {
		job := claim()
		cancelled, stop := context.WithCancel(ctx)
		stop()
		trace.count.Store(0)
		if e := worker.Run(cancelled, job); !errors.Is(e, context.Canceled) || trace.count.Load() != 0 {
			t.Fatal("cancellation", e)
		}
		if state() != seeded {
			t.Fatal("cancelled run changed business rows")
		}
	})
	t.Run("lost lease writes nothing", func(t *testing.T) {
		job := claim()
		stale := job
		stale.LeaseToken = uuid.New()
		if e := worker.Run(ctx, stale); !errors.Is(e, domain.ErrJobLeaseLost) {
			t.Fatal("stale lease", e)
		}
		if state() != seeded || stored(job).State != "running" {
			t.Fatal("stale lease changed business rows or the job")
		}
	})
	t.Run("archives the seed at the frozen instant", func(t *testing.T) {
		job := claim()
		if e := worker.Run(ctx, job); e != nil {
			t.Fatal(e)
		}
		if current := stored(job); current.State != "completed" || current.Result != nil {
			t.Fatal("None completion", current.State)
		}
		archived := state()
		// A later run at the same instant finds nothing left to archive.
		again := claim()
		if e := worker.Run(ctx, again); e != nil || stored(again).State != "completed" || state() != archived {
			t.Fatal("repeated archive changed state", e)
		}
	})
}
