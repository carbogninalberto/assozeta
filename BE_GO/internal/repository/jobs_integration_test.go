//go:build jobsintegration

package repository

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/migrations"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

func TestDurableJobsPostgres(t *testing.T) {
	dsn := os.Getenv("ASSOZETA_JOB_TEST_DSN")
	if dsn == "" {
		t.Fatal("run TESTS/adapters/go/jobs.py; no skipped integration")
	}
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal("invalid private DSN")
	}
	queries := &jobQueryCounter{}
	cfg.Tracer = queries
	db := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	err = db.Ping()
	if err != nil {
		t.Fatal("private PostgreSQL unavailable")
	}
	defer db.Close()
	db.SetMaxOpenConns(4)
	db.SetMaxIdleConns(4)
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	if _, err = db.ExecContext(ctx, `CREATE TABLE contract_business_events (id uuid PRIMARY KEY, amount numeric(12,2) NOT NULL); CREATE TABLE django_migrations (id bigint PRIMARY KEY, app text NOT NULL, name text NOT NULL, applied timestamptz NOT NULL); INSERT INTO django_migrations VALUES (1,'fixture','existing', '2020-01-01Z')`); err != nil {
		t.Fatal(err)
	}
	var starters sync.WaitGroup
	for i := 0; i < 4; i++ {
		starters.Add(1)
		go func() {
			defer starters.Done()
			if e := migrations.Apply(dsn); e != nil {
				t.Error(e)
			}
		}()
	}
	starters.Wait()
	if t.Failed() {
		return
	}
	var version int
	var dirty bool
	if err = db.QueryRowContext(ctx, `SELECT version,dirty FROM assozeta_go_schema_migrations`).Scan(&version, &dirty); err != nil || version != 2 || dirty {
		t.Fatal("separate operational migration history missing", err)
	}
	var legacy int
	if err = db.GetContext(ctx, &legacy, `SELECT count(*) FROM django_migrations WHERE id=1 AND app='fixture' AND name='existing' AND applied='2020-01-01Z'`); err != nil || legacy != 1 {
		t.Fatal("Django migration ownership changed")
	}
	// Trusted private fixture handoff; no production ownership switch is exposed.
	if _, err = db.Exec(`UPDATE assozeta_go_job_ownership SET runtime='go',generation=generation+1 WHERE singleton=1`); err != nil {
		t.Fatal(err)
	}
	queue := NewJobs(db)
	scope := uuid.New()
	foreign := uuid.New()
	intent := func(key string) domain.JobIntent {
		return domain.JobIntent{ID: uuid.New(), AssociationID: scope, Kind: "contract.event", Version: 1, Key: key, Payload: json.RawMessage(`{"n":9007199254740993}`), MaxAttempts: 3}
	}
	enqueue := func(t *testing.T, in domain.JobIntent) domain.Job {
		t.Helper()
		tx, err := db.BeginTxx(ctx, nil)
		if err != nil {
			t.Fatal(err)
		}
		defer tx.Rollback()
		job, err := queue.Enqueue(ctx, tx, in)
		if err != nil {
			t.Fatal(err)
		}
		if err = tx.Commit(); err != nil {
			t.Fatal(err)
		}
		return job
	}
	claim := func(t *testing.T, owner string) domain.Job {
		t.Helper()
		jobs, err := queue.Claim(ctx, []string{"contract.event"}, owner, 1, time.Minute)
		if err != nil || len(jobs) != 1 {
			t.Fatal("claim", err, len(jobs))
		}
		return jobs[0]
	}
	expire := func(t *testing.T, id uuid.UUID) {
		t.Helper()
		if _, err := db.ExecContext(ctx, `UPDATE assozeta_go_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=$1`, id); err != nil {
			t.Fatal(err)
		}
	}
	reset := func(t *testing.T) {
		t.Helper()
		if _, err := db.ExecContext(ctx, `TRUNCATE assozeta_go_jobs`); err != nil {
			t.Fatal(err)
		}
	}
	t.Run("transactional intent and permanent scoped idempotency", func(t *testing.T) {
		defer reset(t)
		in := intent("atomic")
		tx, err := db.BeginTxx(ctx, nil)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = tx.ExecContext(ctx, `INSERT INTO contract_business_events VALUES ($1,12.34)`, in.ID); err != nil {
			t.Fatal(err)
		}
		if _, err = queue.Enqueue(ctx, tx, in); err != nil {
			t.Fatal(err)
		}
		_ = tx.Rollback()
		var count int
		_ = db.GetContext(ctx, &count, `SELECT count(*) FROM contract_business_events`)
		if count != 0 {
			t.Fatal("business rollback failed")
		}
		jobs, err := queue.Claim(ctx, []string{in.Kind}, "worker", 1, time.Minute)
		if err != nil || len(jobs) != 0 {
			t.Fatal("rolled-back job became visible")
		}
		tx, err = db.BeginTxx(ctx, nil)
		if err != nil {
			t.Fatal(err)
		}
		defer tx.Rollback()
		_, err = tx.ExecContext(ctx, `INSERT INTO contract_business_events VALUES ($1,12.34)`, in.ID)
		if err != nil {
			t.Fatal(err)
		}
		first, err := queue.Enqueue(ctx, tx, in)
		if err != nil {
			t.Fatal(err)
		}
		if err = tx.Commit(); err != nil {
			t.Fatal(err)
		}
		var exact map[string]json.Number
		if e := json.Unmarshal(first.Payload, &exact); e != nil || exact["n"].String() != "9007199254740993" {
			t.Fatal("job JSON precision changed")
		}
		again := in
		again.ID = uuid.New()
		again.Payload = json.RawMessage("{ \"n\": 9007199254740993 }")
		if duplicate := enqueue(t, again); duplicate.ID != first.ID {
			t.Fatal("duplicate intent created another job")
		}
		if _, err = queue.Get(ctx, foreign, first.ID); !errors.Is(err, domain.ErrJobNotFound) {
			t.Fatal("foreign job read admitted", err)
		}
		if err = queue.CancelPending(ctx, foreign, first.ID); !errors.Is(err, domain.ErrJobNotFound) {
			t.Fatal("foreign cancel admitted", err)
		}
		changed := again
		changed.Payload = json.RawMessage(`{"n":2}`)
		tx, _ = db.BeginTxx(ctx, nil)
		_, err = queue.Enqueue(ctx, tx, changed)
		_ = tx.Rollback()
		if !errors.Is(err, domain.ErrJobConflict) {
			t.Fatal("idempotency collision replaced payload", err)
		}
		lease := claim(t, "worker")
		if err = queue.Complete(ctx, lease.ID, lease.LeaseToken, json.RawMessage(`{"done":true}`)); err != nil {
			t.Fatal(err)
		}
		if duplicate := enqueue(t, again); duplicate.State != "completed" || duplicate.Attempts != 1 {
			t.Fatal("duplicate resurrected completed work")
		}
		other := in
		other.ID = uuid.New()
		other.AssociationID = foreign
		if enqueue(t, other).ID == first.ID {
			t.Fatal("cross-tenant idempotency collision")
		}
		reset(t)
	})
	t.Run("business effect and completion share the caller transaction", func(t *testing.T) {
		defer reset(t)
		in := intent("effect")
		job := enqueue(t, in)
		lease := claim(t, "worker")
		effect := func(token uuid.UUID, commit bool) error {
			tx, e := db.BeginTxx(ctx, nil)
			if e != nil {
				return e
			}
			defer tx.Rollback()
			if _, e = tx.ExecContext(ctx, `INSERT INTO contract_business_events VALUES ($1,45.67)`, job.ID); e != nil {
				return e
			}
			if e = queue.CompleteInTx(ctx, tx, job.ID, token, json.RawMessage(`{"done":true}`)); e != nil {
				return e
			}
			if commit {
				return tx.Commit()
			}
			return nil
		}
		if e := effect(lease.LeaseToken, false); e != nil {
			t.Fatal(e)
		}
		pending, e := queue.Get(ctx, scope, job.ID)
		if e != nil || pending.State != "running" {
			t.Fatal("rollback committed completion")
		}
		if e = effect(uuid.New(), true); !errors.Is(e, domain.ErrJobLeaseLost) {
			t.Fatal("stale completion admitted business effect", e)
		}
		var count int
		if e = db.GetContext(ctx, &count, `SELECT count(*) FROM contract_business_events WHERE id=$1`, job.ID); e != nil || count != 0 {
			t.Fatal("failed completion leaked effect")
		}
		if e = effect(lease.LeaseToken, true); e != nil {
			t.Fatal(e)
		}
		final, e := queue.Get(ctx, scope, job.ID)
		if e != nil || final.State != "completed" || final.Result == nil || string(*final.Result) != `{"done": true}` {
			t.Fatal("committed completion lost result", e)
		}
		var amount string
		if e = db.GetContext(ctx, &amount, `SELECT amount::text FROM contract_business_events WHERE id=$1`, job.ID); e != nil || amount != "45.67" {
			t.Fatal("transaction lost exact business amount")
		}
		if final.CreatedAt != job.CreatedAt {
			t.Fatal("retry changed original publication time")
		}
		if e = queue.CompleteInTx(ctx, nil, job.ID, lease.LeaseToken, nil); !errors.Is(e, domain.ErrJobInvalid) {
			t.Fatal("missing completion transaction accepted")
		}
		reset(t)
	})
	t.Run("mixed kinds and states preserve bounded due selection", func(t *testing.T) {
		defer reset(t)
		first := enqueue(t, intent("mixed-first"))
		secondIn := intent("mixed-second")
		secondIn.Kind = "contract.other"
		second := enqueue(t, secondIn)
		lease := claim(t, "worker")
		if lease.ID != first.ID {
			t.Fatal("kind isolation failed")
		}
		expire(t, first.ID)
		future := intent("mixed-future")
		future.AvailableAt = time.Now().Add(time.Hour)
		enqueue(t, future)
		jobs, e := queue.Claim(ctx, []string{"contract.event", "contract.other", "contract.event"}, "worker", 2, time.Minute)
		if e != nil || len(jobs) != 2 {
			t.Fatal("mixed states lost eligible work", e)
		}
		ids := map[uuid.UUID]bool{}
		for _, j := range jobs {
			ids[j.ID] = true
		}
		if !ids[first.ID] || !ids[second.ID] {
			t.Fatal("duplicate kinds or future work changed claims")
		}
		reset(t)
	})

	t.Run("concurrent claims skip locked rows and never duplicate live leases", func(t *testing.T) {
		defer reset(t)
		for i := 0; i < 32; i++ {
			enqueue(t, intent(uuid.NewString()))
		}
		tx, _ := db.BeginTxx(ctx, nil)
		defer tx.Rollback()
		var held uuid.UUID
		if err = db.GetContext(ctx, &held, `SELECT id FROM assozeta_go_jobs ORDER BY id LIMIT 1`); err != nil {
			t.Fatal(err)
		}
		if _, err = tx.ExecContext(ctx, `SELECT id FROM assozeta_go_jobs WHERE id=$1 FOR UPDATE`, held); err != nil {
			t.Fatal(err)
		}
		var mu sync.Mutex
		seen := map[uuid.UUID]bool{}
		var wg sync.WaitGroup
		for i := 0; i < 8; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				for {
					jobs, e := queue.Claim(ctx, []string{"contract.event"}, uuid.NewString(), 4, time.Minute)
					if e != nil {
						t.Error(e)
						return
					}
					if len(jobs) == 0 {
						return
					}
					mu.Lock()
					for _, j := range jobs {
						if seen[j.ID] || j.ID == held || j.Attempts != 1 {
							t.Error("duplicate or locked claim")
						}
						seen[j.ID] = true
					}
					mu.Unlock()
				}
			}()
		}
		wg.Wait()
		if len(seen) != 31 || db.Stats().OpenConnections > 4 {
			t.Fatal("claim/pool bound changed", len(seen))
		}
		_ = tx.Rollback()
		last := claim(t, "last")
		if last.ID != held {
			t.Fatal("locked row lost")
		}
		reset(t)
	})
	t.Run("lease token expiry renewal recovery and stale completion", func(t *testing.T) {
		defer reset(t)
		job := enqueue(t, intent("crash"))
		first := claim(t, "same-worker")
		if err = queue.Renew(ctx, job.ID, uuid.New(), time.Minute); !errors.Is(err, domain.ErrJobLeaseLost) {
			t.Fatal("forged token renewed", err)
		}
		if err = queue.Renew(ctx, job.ID, first.LeaseToken, 2*time.Minute); err != nil {
			t.Fatal(err)
		}
		extended, e := queue.Get(ctx, scope, job.ID)
		if e != nil || extended.LeaseUntil == nil || !extended.LeaseUntil.After(*first.LeaseUntil) {
			t.Fatal("renewal did not extend lease")
		}
		if e = queue.Renew(ctx, job.ID, first.LeaseToken, time.Millisecond); e != nil {
			t.Fatal(e)
		}
		shorter, e := queue.Get(ctx, scope, job.ID)
		if e != nil || shorter.LeaseUntil == nil || shorter.LeaseUntil.Before(*extended.LeaseUntil) {
			t.Fatal("renewal shortened an existing lease")
		}
		expire(t, job.ID)
		if err = queue.Complete(ctx, job.ID, first.LeaseToken, nil); !errors.Is(err, domain.ErrJobLeaseLost) {
			t.Fatal("expired lease completed", err)
		}
		if err = queue.Renew(ctx, job.ID, first.LeaseToken, time.Minute); !errors.Is(err, domain.ErrJobLeaseLost) {
			t.Fatal("expired lease resurrected", err)
		}
		second := claim(t, "same-worker")
		if second.LeaseToken == first.LeaseToken || second.Attempts != 2 {
			t.Fatal("crash recovery reused fencing token")
		}
		if err = queue.Fail(ctx, job.ID, first.LeaseToken, "old_worker", true, 0); !errors.Is(err, domain.ErrJobLeaseLost) {
			t.Fatal("stale worker changed recovered job", err)
		}
		if err = queue.Complete(ctx, job.ID, second.LeaseToken, nil); err != nil {
			t.Fatal(err)
		}
		reset(t)
	})
	t.Run("bounded retry delayed eligibility and exhausted crash recovery", func(t *testing.T) {
		defer reset(t)
		in := intent("retry")
		in.MaxAttempts = 2
		job := enqueue(t, in)
		first := claim(t, "worker")
		if err = queue.Fail(ctx, job.ID, first.LeaseToken, "provider_unavailable", true, time.Hour); err != nil {
			t.Fatal(err)
		}
		jobs, e := queue.Claim(ctx, []string{in.Kind}, "worker", 1, time.Minute)
		if e != nil || len(jobs) != 0 {
			t.Fatal("backoff ignored")
		}
		_, _ = db.ExecContext(ctx, `UPDATE assozeta_go_jobs SET available_at=clock_timestamp() WHERE id=$1`, job.ID)
		second := claim(t, "worker")
		expire(t, job.ID)
		jobs, e = queue.Claim(ctx, []string{in.Kind}, "worker", 1, time.Minute)
		if e != nil || len(jobs) != 0 {
			t.Fatal("exhausted crash reclaimed", e)
		}
		final, e := queue.Get(ctx, scope, job.ID)
		if e != nil || final.State != "failed" || final.Attempts != 2 || final.ErrorCode != "lease_expired" || second.Attempts != 2 {
			t.Fatal("crash exhaustion not terminal", e, final.State)
		}
		reset(t)
	})
	t.Run("kind isolation future work cancellation and validation", func(t *testing.T) {
		defer reset(t)
		in := intent("future")
		in.AvailableAt = time.Now().Add(time.Hour)
		job := enqueue(t, in)
		jobs, e := queue.Claim(ctx, []string{in.Kind}, "worker", 1, time.Minute)
		if e != nil || len(jobs) != 0 {
			t.Fatal("future job claimed")
		}
		if e = queue.CancelPending(ctx, scope, job.ID); e != nil {
			t.Fatal(e)
		}
		if e = queue.CancelPending(ctx, scope, job.ID); e != nil {
			t.Fatal("cancel not idempotent", e)
		}
		next := enqueue(t, intent("live"))
		jobs, e = queue.Claim(ctx, []string{"unknown.kind"}, "worker", 1, time.Minute)
		if e != nil || len(jobs) != 0 {
			t.Fatal("unregistered kind claimed")
		}
		lease := claim(t, "worker")
		if e = queue.CancelPending(ctx, scope, next.ID); !errors.Is(e, domain.ErrJobConflict) {
			t.Fatal("running cancellation was silently accepted", e)
		}
		if e = queue.Fail(ctx, lease.ID, lease.LeaseToken, "permanent", false, 0); e != nil {
			t.Fatal(e)
		}
		for _, size := range []int{0, 65} {
			if _, e = queue.Claim(ctx, []string{in.Kind}, "worker", size, time.Minute); !errors.Is(e, domain.ErrJobInvalid) {
				t.Fatal("unbounded claim accepted")
			}
		}
		if _, e = queue.Enqueue(ctx, nil, intent("no-tx")); !errors.Is(e, domain.ErrJobInvalid) {
			t.Fatal("intent escaped caller transaction")
		}
		reset(t)
	})
	t.Run("killed claiming process leaves recoverable durable intent", func(t *testing.T) {
		defer reset(t)
		job := enqueue(t, intent("killed"))
		cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestDurableJobCrashChild$")
		cmd.Env = append(os.Environ(), "ASSOZETA_JOB_CRASH_CHILD=1")
		stdout, e := cmd.StdoutPipe()
		if e != nil {
			t.Fatal(e)
		}
		stdin, e := cmd.StdinPipe()
		if e != nil {
			t.Fatal(e)
		}
		cmd.Stderr = os.Stderr
		if e = cmd.Start(); e != nil {
			t.Fatal(e)
		}
		defer func() {
			_ = stdin.Close()
			if cmd.ProcessState == nil {
				_ = cmd.Process.Kill()
				_ = cmd.Wait()
			}
		}()
		ready := make(chan string, 1)
		go func() { line, _ := bufio.NewReader(stdout).ReadString('\n'); ready <- strings.TrimSpace(line) }()
		select {
		case line := <-ready:
			if line != "job-lease-ready" {
				t.Fatal("child did not claim")
			}
		case <-time.After(5 * time.Second):
			t.Fatal("child startup timeout")
		}
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
		var recovered []domain.Job
		deadline := time.Now().Add(3 * time.Second)
		for time.Now().Before(deadline) {
			recovered, e = queue.Claim(ctx, []string{"contract.event"}, "replacement", 1, time.Minute)
			if e != nil {
				t.Fatal(e)
			}
			if len(recovered) > 0 {
				break
			}
			time.Sleep(10 * time.Millisecond)
		}
		if len(recovered) != 1 || recovered[0].ID != job.ID || recovered[0].Attempts != 2 {
			t.Fatal("killed worker lost durable intent")
		}
		reset(t)
	})
	t.Run("bounded queries and additive migration ownership", func(t *testing.T) {
		defer reset(t)
		for _, size := range []int{1, 16, 128, 512} {
			for i := 0; i < size; i++ {
				enqueue(t, intent(uuid.NewString()))
			}
			queries.count.Store(0)
			jobs, e := queue.Claim(ctx, []string{"contract.event"}, "batch", 1, time.Minute)
			if e != nil || len(jobs) != 1 || queries.count.Load() != 1 {
				t.Fatal("claim added per-record queries", e)
			}
			reset(t)
		}
		if _, e := db.ExecContext(ctx, `INSERT INTO assozeta_go_jobs (id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at) SELECT md5(n::text)::uuid,$1,'contract.event',1,n::text,'{}'::jsonb,3,clock_timestamp() FROM generate_series(1,4096) n`, scope); e != nil {
			t.Fatal(e)
		}
		if _, e := db.ExecContext(ctx, `ANALYZE assozeta_go_jobs`); e != nil {
			t.Fatal(e)
		}
		var plan json.RawMessage
		e := db.GetContext(ctx, &plan, "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) "+claimJobsSQL, []string{"contract.event"}, 4, "plan-worker", uuid.New(), int64(60000))
		if e != nil {
			t.Fatal(e)
		}
		if path := os.Getenv("ASSOZETA_JOB_PLAN_OUTPUT"); path == "" || os.WriteFile(path, plan, 0600) != nil {
			t.Fatal("query plan evidence missing")
		}
		var parsed any
		if e = json.Unmarshal(plan, &parsed); e != nil {
			t.Fatal(e)
		}
		var inspect func(any)
		inspect = func(value any) {
			switch node := value.(type) {
			case map[string]any:
				if n, ok := node["Actual Rows"].(float64); ok && n > 64 {
					t.Errorf("claim materialized unbounded rows: %s %v", node["Node Type"], n)
				}
				for _, v := range node {
					inspect(v)
				}
			case []any:
				for _, v := range node {
					inspect(v)
				}
			}
		}
		inspect(parsed)
		down, e := os.ReadFile("../../migrations/000001_durable_jobs.down.sql")
		if e != nil {
			t.Fatal(e)
		}
		if _, e = db.ExecContext(ctx, string(down)); e == nil {
			t.Fatal("down migration discarded durable jobs")
		}
		var count int
		if e = db.GetContext(ctx, &count, `SELECT count(*) FROM assozeta_go_jobs`); e != nil || count != 4096 {
			t.Fatal("rollback guard lost jobs")
		}
		cancelled, cancelNow := context.WithCancel(ctx)
		cancelNow()
		if _, e = queue.Claim(cancelled, []string{"contract.event"}, "worker", 1, time.Minute); !errors.Is(e, context.Canceled) {
			t.Fatal("claim ignored cancellation", e)
		}
		reset(t)
	})

	t.Run("duplicate publishers preserve one durable job", func(t *testing.T) {
		defer reset(t)
		in := intent("duplicate")
		var wg sync.WaitGroup
		ids := make(chan uuid.UUID, 16)
		for i := 0; i < 16; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				tx, e := db.BeginTxx(ctx, nil)
				if e != nil {
					t.Error(e)
					return
				}
				defer tx.Rollback()
				local := in
				local.ID = uuid.New()
				job, e := queue.Enqueue(ctx, tx, local)
				if e != nil {
					t.Error(e)
					return
				}
				if e = tx.Commit(); e != nil {
					t.Error(e)
					return
				}
				ids <- job.ID
			}()
		}
		wg.Wait()
		close(ids)
		var first uuid.UUID
		count := 0
		for id := range ids {
			count++
			if first == uuid.Nil {
				first = id
			}
			if id != first {
				t.Fatal("duplicate publishers diverged")
			}
		}
		if count != 16 {
			t.Fatal("publisher lost")
		}
		reset(t)
	})
}

// Child process has no opportunity for graceful cleanup after SIGKILL.
func TestDurableJobCrashChild(t *testing.T) {
	if os.Getenv("ASSOZETA_JOB_CRASH_CHILD") == "" {
		return
	}
	db, err := sqlx.Connect("pgx", os.Getenv("ASSOZETA_JOB_TEST_DSN"))
	if err != nil {
		t.Fatal("private child database unavailable")
	}
	defer db.Close()
	jobs, err := NewJobs(db).Claim(context.Background(), []string{"contract.event"}, "crashed-worker", 1, 200*time.Millisecond)
	if err != nil || len(jobs) != 1 {
		t.Fatal("child cannot claim")
	}
	_, _ = io.WriteString(os.Stdout, "job-lease-ready\n")
	_, _ = io.Copy(io.Discard, os.Stdin)
}

type jobQueryCounter struct{ count atomic.Int64 }

func (q *jobQueryCounter) TraceQueryStart(ctx context.Context, _ *pgx.Conn, _ pgx.TraceQueryStartData) context.Context {
	q.count.Add(1)
	return ctx
}
func (*jobQueryCounter) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}
