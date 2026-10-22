//go:build emailbalanceintegration

package service

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/migrations"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

type resetQueries struct{ count atomic.Int64 }

func (q *resetQueries) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	q.count.Add(1)
	return ctx
}
func (*resetQueries) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

func TestEmailBalanceResetPostgres(t *testing.T) {
	dsn := os.Getenv("ASSOZETA_EMAIL_BALANCE_DSN")
	if dsn == "" {
		t.Fatal("run accounts.py --only email-balance-reset")
	}
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	trace := &resetQueries{}
	cfg.Tracer = trace
	db := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	db.SetMaxIdleConns(4)
	if err = migrations.Apply(dsn); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	// Trusted private fixture handoff; no production ownership switch is exposed.
	if _, err = db.Exec(`UPDATE assozeta_go_job_ownership SET runtime='go',generation=generation+1 WHERE singleton=1`); err != nil {
		t.Fatal(err)
	}
	queue := repository.NewJobs(db)
	var messages bytes.Buffer
	reset := NewEmailBalanceReset(repository.NewEmailBalanceResets(db), slog.New(slog.NewTextHandler(&messages, nil)))
	exec := func(sql string, args ...any) {
		t.Helper()
		if _, e := db.ExecContext(ctx, sql, args...); e != nil {
			t.Fatal(e)
		}
	}
	var initial int
	if err = db.GetContext(ctx, &initial, `SELECT count(*) FROM assozeta_go_jobs`); err != nil || initial != 0 {
		t.Fatal("private job table must be empty")
	}
	defer exec(`DELETE FROM assozeta_go_jobs`)
	intent := func() domain.JobIntent {
		return domain.JobIntent{ID: uuid.New(), Kind: domain.JobResetDailyEmailBalance, Version: 1, Key: uuid.NewString(), Payload: json.RawMessage(`{}`), MaxAttempts: 1}
	}
	publish := func(in domain.JobIntent) domain.Job {
		t.Helper()
		tx, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer tx.Rollback()
		j, e := queue.Enqueue(ctx, tx, in)
		if e != nil {
			t.Fatal(e)
		}
		if e = tx.Commit(); e != nil {
			t.Fatal(e)
		}
		return j
	}
	claim := func() domain.Job {
		t.Helper()
		publish(intent())
		jobs, e := queue.Claim(ctx, []string{domain.JobResetDailyEmailBalance}, "reset-fixture", 1, time.Minute)
		if e != nil || len(jobs) != 1 {
			t.Fatal("claim", e)
		}
		return jobs[0]
	}
	state := func(j domain.Job) domain.Job {
		t.Helper()
		got, e := queue.Get(ctx, j.AssociationID, j.ID)
		if e != nil {
			t.Fatal(e)
		}
		return got
	}
	balances := func(want int) {
		t.Helper()
		var count int
		if e := db.GetContext(ctx, &count, `SELECT count(*) FROM communications_communicationconfiguration WHERE daily_email_balance<>$1`, want); e != nil || count != 0 {
			t.Fatalf("counters differ from %d: %d %v", want, count, e)
		}
	}
	clearJobs := func() { exec(`DELETE FROM assozeta_go_jobs`) }

	t.Run("bulk reset and Django readback", func(t *testing.T) {
		in := intent()
		job := publish(in)
		gate := NewJobRestoreAdmission(repository.NewRestoreFileLock(filepath.Join(t.TempDir(), "restore.lock")), repository.NewRestoreStates(db), true)
		worker, e := NewJobExecutor(queue, gate, map[string]JobDefinition{domain.JobResetDailyEmailBalance: {Version: 1, Timeout: time.Second, Run: reset.Run}}, JobExecutorOptions{Owner: "reset-fixture", Concurrency: 1, Lease: time.Second, RenewInterval: 100 * time.Millisecond, StoreTimeout: 200 * time.Millisecond, PollInterval: time.Millisecond, MaintenanceInterval: 15 * time.Second, DrainTimeout: time.Second})
		if e != nil {
			t.Fatal(e)
		}
		if ran, e := worker.ExecuteNext(ctx); e != nil || !ran {
			t.Fatal("reset not dispatched", e)
		}
		got := state(job)
		if got.State != "completed" || got.Result != nil {
			t.Fatal("legacy None result not retained")
		}
		balances(0)
	})
	t.Run("invalid job scope and payload have no effects", func(t *testing.T) {
		defer clearJobs()
		exec(`UPDATE communications_communicationconfiguration SET daily_email_balance=9`)
		job := claim()
		cases := []domain.Job{job, job, job, job, job, job}
		cases[0].AssociationID = uuid.New()
		cases[1].Payload = json.RawMessage(`{"association":"forged"}`)
		cases[2].Payload = json.RawMessage(`null`)
		cases[3].Version = 2
		cases[4].Kind = "unrelated"
		cases[5].LeaseToken = uuid.Nil
		for _, j := range cases {
			trace.count.Store(0)
			if e := reset.Run(ctx, j); !errors.Is(e, domain.ErrJobInvalid) {
				t.Fatal("invalid input accepted", e)
			}
			if trace.count.Load() != 0 {
				t.Fatal("invalid input reached database")
			}
		}
		balances(9)
	})
	t.Run("completed occurrence cannot erase later usage", func(t *testing.T) {
		defer clearJobs()
		in := intent()
		publish(in)
		jobs, e := queue.Claim(ctx, []string{domain.JobResetDailyEmailBalance}, "reset-fixture", 1, time.Minute)
		if e != nil || len(jobs) != 1 {
			t.Fatal(e)
		}
		job := jobs[0]
		if e = reset.Run(ctx, job); e != nil {
			t.Fatal(e)
		}
		exec(`UPDATE communications_communicationconfiguration SET daily_email_balance=7`)
		if e = reset.Run(ctx, job); !errors.Is(e, domain.ErrJobLeaseLost) {
			t.Fatal("completed attempt replay accepted", e)
		}
		replay := publish(in)
		if replay.ID != job.ID || replay.State != "completed" {
			t.Fatal("occurrence was reopened")
		}
		jobs, e = queue.Claim(ctx, []string{domain.JobResetDailyEmailBalance}, "reset-fixture", 1, time.Minute)
		if e != nil || len(jobs) != 0 {
			t.Fatal("duplicate occurrence claimed", e)
		}
		balances(7)
	})
	t.Run("stale completion rolls back counters", func(t *testing.T) {
		defer clearJobs()
		exec(`UPDATE communications_communicationconfiguration SET daily_email_balance=11`)
		job := claim()
		exec(`UPDATE assozeta_go_jobs SET lease_token=$2 WHERE id=$1`, job.ID, uuid.New())
		if e := reset.Run(ctx, job); !errors.Is(e, domain.ErrJobLeaseLost) {
			t.Fatal("stale attempt committed", e)
		}
		balances(11)
	})
	t.Run("legacy SQL failure completes without partial reset", func(t *testing.T) {
		defer clearJobs()
		exec(`UPDATE communications_communicationconfiguration SET daily_email_balance=13`)
		exec(`CREATE FUNCTION contract_reset_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'private fixture failure'; END $$`)
		defer exec(`DROP FUNCTION contract_reset_failure()`)
		exec(`CREATE TRIGGER contract_reset_failure BEFORE UPDATE ON communications_communicationconfiguration FOR EACH STATEMENT EXECUTE FUNCTION contract_reset_failure()`)
		defer exec(`DROP TRIGGER contract_reset_failure ON communications_communicationconfiguration`)
		job := claim()
		messages.Reset()
		if e := reset.Run(ctx, job); e != nil {
			t.Fatal("Django swallows reset query failure", e)
		}
		got := state(job)
		if got.State != "completed" || got.Result != nil {
			t.Fatal("handled legacy failure result changed")
		}
		balances(13)
		if output := messages.String(); !strings.Contains(output, "email_balance_reset_failed") || strings.Contains(output, "private fixture failure") {
			t.Fatal("handled failure diagnostics missing or disclose raw database details")
		}
	})
	t.Run("cancelled database work is recoverable", func(t *testing.T) {
		defer clearJobs()
		job := claim()
		lock, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer lock.Rollback()
		if _, e = lock.ExecContext(ctx, `LOCK TABLE communications_communicationconfiguration IN ACCESS EXCLUSIVE MODE`); e != nil {
			t.Fatal(e)
		}
		timeout, stop := context.WithTimeout(ctx, 50*time.Millisecond)
		e = reset.Run(timeout, job)
		stop()
		if e == nil {
			t.Fatal("cancelled work reported successful completion")
		}
		if e = lock.Rollback(); e != nil {
			t.Fatal(e)
		}
		if state(job).State != "running" {
			t.Fatal("cancelled execution lost recoverable lease")
		}
		balances(13)
		if e = reset.Run(ctx, job); e != nil {
			t.Fatal("pool or transaction did not recover", e)
		}
		balances(0)
	})
	t.Run("bounded query count across increasing sizes", func(t *testing.T) {
		defer clearJobs()
		for _, n := range []int{0, 1, 16, 128, 512} {
			exec(`UPDATE communications_communicationconfiguration SET daily_email_balance=0`)
			exec(`UPDATE communications_communicationconfiguration SET daily_email_balance=5 WHERE communication_configuration_id IN (SELECT communication_configuration_id FROM communications_communicationconfiguration ORDER BY communication_configuration_id LIMIT $1)`, n)
			job := claim()
			trace.count.Store(0)
			if e := reset.Run(ctx, job); e != nil {
				t.Fatal(e)
			}
			if got := trace.count.Load(); got != 5 {
				t.Fatalf("%d positive counters: %d queries, want BEGIN/ownership fence/UPDATE/complete/COMMIT", n, got)
			}
			balances(0)
		}
		var plan string
		rows, e := db.QueryxContext(ctx, `EXPLAIN UPDATE communications_communicationconfiguration SET daily_email_balance=0 WHERE daily_email_balance>0`)
		if e != nil {
			t.Fatal(e)
		}
		defer rows.Close()
		for rows.Next() {
			var line string
			if e = rows.Scan(&line); e != nil {
				t.Fatal(e)
			}
			plan += line + "\n"
		}
		if e = rows.Err(); e != nil {
			t.Fatal(e)
		}
		if !strings.Contains(plan, "Update on communications_communicationconfiguration") || !strings.Contains(plan, "daily_email_balance > 0") {
			t.Fatal("unexpected bulk update plan")
		}
		t.Log("Instance-wide bulk reset plan (all configurations are intentional scope):\n" + plan)
	})
}
