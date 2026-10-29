//go:build jobsintegration

package service

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/migrations"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

func TestJobExecutorPostgres(t *testing.T) {
	dsn := os.Getenv("ASSOZETA_JOB_TEST_DSN")
	if dsn == "" {
		t.Fatal("run TESTS/adapters/go/jobs.py")
	}
	dbConfig, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal("private PostgreSQL unavailable")
	}
	db := sqlx.NewDb(ownedpgx.OpenDB(*dbConfig), "pgx")
	if err = db.Ping(); err != nil {
		_ = db.Close()
		t.Fatal("private PostgreSQL unavailable")
	}
	defer db.Close()
	db.SetMaxOpenConns(4)
	db.SetMaxIdleConns(4)
	if err = migrations.Apply(dsn); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	if _, err = db.ExecContext(ctx, `CREATE TABLE executor_effects(id uuid PRIMARY KEY,association_id uuid NOT NULL,amount numeric(10,2) NOT NULL); CREATE TABLE instance_datarestore(id uuid PRIMARY KEY,state text NOT NULL,completed_at timestamptz)`); err != nil {
		t.Fatal(err)
	}
	// Trusted private fixture handoff; no production ownership switch is exposed.
	if _, err = db.Exec(`UPDATE assozeta_go_job_ownership SET runtime='go',generation=generation+1 WHERE singleton=1`); err != nil {
		t.Fatal(err)
	}
	queue := repository.NewJobs(db)
	scope := uuid.New()
	locks := repository.NewRestoreFileLock(filepath.Join(t.TempDir(), "shared-restore.lock"))
	gate := NewJobRestoreAdmission(locks, repository.NewRestoreStates(db), true)
	options := JobExecutorOptions{Owner: "contract-worker", Concurrency: 1, Lease: time.Second, RenewInterval: 100 * time.Millisecond, StoreTimeout: 300 * time.Millisecond, PollInterval: 10 * time.Millisecond, MaintenanceInterval: 20 * time.Millisecond, DrainTimeout: 150 * time.Millisecond}
	reset := func(t *testing.T) {
		t.Helper()
		if _, e := db.ExecContext(ctx, `TRUNCATE assozeta_go_periodic_occurrences,assozeta_go_jobs,instance_datarestore,executor_effects`); e != nil {
			t.Error(e)
		}
	}
	enqueue := func(t *testing.T, version int) domain.Job {
		t.Helper()
		tx, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer tx.Rollback()
		job, e := queue.Enqueue(ctx, tx, domain.JobIntent{ID: uuid.New(), AssociationID: scope, Kind: "contract.execute", Version: version, Key: uuid.NewString(), Payload: json.RawMessage(`{"value":9007199254740993}`), MaxAttempts: 3})
		if e != nil {
			t.Fatal(e)
		}
		if e = tx.Commit(); e != nil {
			t.Fatal(e)
		}
		return job
	}
	executor := func(t *testing.T, run func(context.Context, domain.Job) error, timeout time.Duration) *JobExecutor {
		t.Helper()
		worker, e := NewJobExecutor(queue, gate, map[string]JobDefinition{"contract.execute": {Version: 1, Timeout: timeout, Run: run}}, options)
		if e != nil {
			t.Fatal(e)
		}
		return worker
	}
	complete := func(ctx context.Context, job domain.Job) error {
		return queue.Complete(ctx, job.ID, job.LeaseToken, json.RawMessage(`{"done":true}`))
	}
	state := func(t *testing.T, id uuid.UUID) domain.Job {
		t.Helper()
		j, e := queue.Get(ctx, scope, id)
		if e != nil {
			t.Fatal(e)
		}
		return j
	}
	receive := func(t *testing.T, ch <-chan error) error {
		t.Helper()
		select {
		case e := <-ch:
			return e
		case <-time.After(5 * time.Second):
			t.Fatal("executor did not finish")
			return nil
		}
	}
	await := func(t *testing.T, ch <-chan struct{}) {
		t.Helper()
		select {
		case <-ch:
		case <-time.After(5 * time.Second):
			t.Fatal("handler event timed out")
		}
	}

	t.Run("service owns atomic effects and completion", func(t *testing.T) {
		defer reset(t)
		job := enqueue(t, 1)
		worker := executor(t, func(ctx context.Context, j domain.Job) error {
			if j.AssociationID != scope || j.Attempts != 1 {
				return errors.New("wrong trusted job scope")
			}
			tx, e := db.BeginTxx(ctx, nil)
			if e != nil {
				return e
			}
			defer tx.Rollback()
			if _, e = tx.ExecContext(ctx, `INSERT INTO executor_effects VALUES ($1,$2,12.34)`, j.ID, j.AssociationID); e != nil {
				return e
			}
			if e = queue.CompleteInTx(ctx, tx, j.ID, j.LeaseToken, json.RawMessage(`{"done":true}`)); e != nil {
				return e
			}
			return tx.Commit()
		}, time.Second)
		ran, e := worker.ExecuteNext(ctx)
		if e != nil || !ran || state(t, job.ID).State != "completed" {
			t.Fatal("service completion failed", e)
		}
		var amount string
		if e = db.GetContext(ctx, &amount, `SELECT amount::text FROM executor_effects WHERE id=$1 AND association_id=$2`, job.ID, scope); e != nil || amount != "12.34" {
			t.Fatal("atomic effect lost")
		}
	})
	t.Run("declared retries retain publication time", func(t *testing.T) {
		defer reset(t)
		job := enqueue(t, 1)
		var calls int
		worker := executor(t, func(ctx context.Context, j domain.Job) error {
			calls++
			if calls == 1 {
				return &domain.JobFailure{Code: "provider_unavailable", Retry: true, Delay: time.Hour}
			}
			return complete(ctx, j)
		}, time.Second)
		if _, e := worker.ExecuteNext(ctx); e != nil {
			t.Fatal(e)
		}
		retry := state(t, job.ID)
		if retry.State != "pending" || retry.Attempts != 1 || retry.CreatedAt != job.CreatedAt {
			t.Fatal("retry lost original intent")
		}
		if ran, e := worker.ExecuteNext(ctx); e != nil || ran {
			t.Fatal("backoff ignored")
		}
		_, _ = db.ExecContext(ctx, `UPDATE assozeta_go_jobs SET available_at=clock_timestamp() WHERE id=$1`, job.ID)
		if _, e := worker.ExecuteNext(ctx); e != nil || state(t, job.ID).State != "completed" || calls != 2 {
			t.Fatal("retry did not complete", e)
		}
	})
	t.Run("version panic timeout and missing completion fail explicitly", func(t *testing.T) {
		defer reset(t)
		cases := []struct {
			version int
			code    string
			run     func(context.Context, domain.Job) error
			timeout time.Duration
		}{
			{2, "unsupported_version", func(context.Context, domain.Job) error { t.Error("unsupported version executed"); return nil }, time.Second},
			{1, "task_panicked", func(context.Context, domain.Job) error { panic("fixture-private payload") }, time.Second},
			{1, "completion_missing", func(context.Context, domain.Job) error { return nil }, time.Second},
			{1, "task_timeout", func(ctx context.Context, _ domain.Job) error { <-ctx.Done(); return ctx.Err() }, 20 * time.Millisecond},
		}
		for _, c := range cases {
			job := enqueue(t, c.version)
			worker := executor(t, c.run, c.timeout)
			if _, e := worker.ExecuteNext(ctx); e != nil {
				t.Fatal(e)
			}
			got := state(t, job.ID)
			if got.State != "failed" || got.ErrorCode != c.code {
				t.Fatal("unexpected terminal failure", got.State, got.ErrorCode, c.code)
			}
		}
	})
	t.Run("heartbeat retains a long running job", func(t *testing.T) {
		defer reset(t)
		job := enqueue(t, 1)
		entered, release := make(chan struct{}), make(chan struct{}, 1)
		defer close(release)
		worker := executor(t, func(ctx context.Context, j domain.Job) error {
			close(entered)
			select {
			case <-release:
				return complete(ctx, j)
			case <-ctx.Done():
				return ctx.Err()
			}
		}, 5*time.Second)
		done := make(chan error, 1)
		go func() { _, e := worker.ExecuteNext(ctx); done <- e }()
		await(t, entered)
		time.Sleep(1200 * time.Millisecond)
		other, e := queue.Claim(ctx, []string{"contract.execute"}, "competing", 1, time.Second)
		if e != nil || len(other) != 0 || state(t, job.ID).Attempts != 1 {
			t.Fatal("live job reclaimed", e)
		}
		release <- struct{}{}
		if e = receive(t, done); e != nil || state(t, job.ID).State != "completed" {
			t.Fatal(e)
		}
	})
	t.Run("lease loss cancels the handler without stale writes", func(t *testing.T) {
		defer reset(t)
		job := enqueue(t, 1)
		entered := make(chan struct{})
		worker := executor(t, func(ctx context.Context, _ domain.Job) error { close(entered); <-ctx.Done(); return ctx.Err() }, 5*time.Second)
		done := make(chan error, 1)
		go func() { _, e := worker.ExecuteNext(ctx); done <- e }()
		await(t, entered)
		if _, e := db.ExecContext(ctx, `UPDATE assozeta_go_jobs SET lease_token=$2 WHERE id=$1`, job.ID, uuid.New()); e != nil {
			t.Fatal(e)
		}
		if e := receive(t, done); !errors.Is(e, domain.ErrJobLeaseLost) {
			t.Fatal("stale worker did not fail closed", e)
		}
		if state(t, job.ID).State != "running" {
			t.Fatal("stale worker changed durable outcome")
		}
	})
	t.Run("shutdown preserves an admitted lease failure", func(t *testing.T) {
		defer reset(t)
		job := enqueue(t, 1)
		entered := make(chan struct{})
		stopCtx, stop := context.WithCancel(ctx)
		defer stop()
		worker := executor(t, func(ctx context.Context, _ domain.Job) error { close(entered); <-ctx.Done(); stop(); return ctx.Err() }, 5*time.Second)
		done := make(chan error, 1)
		go func() { done <- worker.Run(stopCtx) }()
		await(t, entered)
		if _, e := db.ExecContext(ctx, `UPDATE assozeta_go_jobs SET lease_token=$2 WHERE id=$1`, job.ID, uuid.New()); e != nil {
			t.Fatal(e)
		}
		if e := receive(t, done); !errors.Is(e, domain.ErrJobLeaseLost) {
			t.Fatal("shutdown hid an admitted lease failure", e)
		}
	})

	t.Run("blocked heartbeat fails closed and database pool recovers", func(t *testing.T) {
		defer reset(t)
		job := enqueue(t, 1)
		entered := make(chan struct{})
		worker := executor(t, func(ctx context.Context, _ domain.Job) error { close(entered); <-ctx.Done(); return ctx.Err() }, 5*time.Second)
		done := make(chan error, 1)
		go func() { _, e := worker.ExecuteNext(ctx); done <- e }()
		await(t, entered)
		tx, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer tx.Rollback()
		if _, e = tx.ExecContext(ctx, `LOCK TABLE assozeta_go_jobs IN ACCESS EXCLUSIVE MODE`); e != nil {
			t.Fatal(e)
		}
		if e = receive(t, done); e == nil {
			t.Fatal("blocked heartbeat did not cancel execution")
		}
		_ = tx.Rollback()
		if state(t, job.ID).State != "running" {
			t.Fatal("heartbeat failure fabricated an outcome")
		}
		lease, e := locks.Acquire(ctx, true, true)
		if e != nil {
			t.Fatal("failed handler leaked restore lease")
		}
		_ = lease.Close()
	})
	t.Run("disabled restore coordination and database failure preserve admission", func(t *testing.T) {
		defer reset(t)
		job := enqueue(t, 1)
		closed, e := sqlx.Connect("pgx", dsn)
		if e != nil {
			t.Fatal(e)
		}
		_ = closed.Close()
		broken := NewJobRestoreAdmission(locks, repository.NewRestoreStates(closed), true)
		worker, e := NewJobExecutor(queue, broken, map[string]JobDefinition{"contract.execute": {Version: 1, Timeout: time.Second, Run: complete}}, options)
		if e != nil {
			t.Fatal(e)
		}
		if ran, e := worker.ExecuteNext(ctx); ran || e == nil || state(t, job.ID).Attempts != 0 {
			t.Fatal("admission database error consumed work")
		}
		lease, e := locks.Acquire(ctx, true, true)
		if e != nil {
			t.Fatal("admission failure leaked restore lease")
		}
		_ = lease.Close()
		_, e = db.ExecContext(ctx, `INSERT INTO instance_datarestore VALUES ($1,'queued',NULL)`, uuid.New())
		if e != nil {
			t.Fatal(e)
		}
		disabled := NewJobRestoreAdmission(repository.NewRestoreFileLock(""), repository.NewRestoreStates(closed), false)
		worker, e = NewJobExecutor(queue, disabled, map[string]JobDefinition{"contract.execute": {Version: 1, Timeout: time.Second, Run: complete}}, options)
		if e != nil {
			t.Fatal(e)
		}
		if _, e = worker.ExecuteNext(ctx); e != nil || state(t, job.ID).State != "completed" {
			t.Fatal("disabled coordination unexpectedly queried state", e)
		}
	})

	t.Run("maintenance defers before claim without spending attempts", func(t *testing.T) {
		defer reset(t)
		job := enqueue(t, 1)
		worker := executor(t, complete, time.Second)
		exclusive, e := locks.Acquire(ctx, true, true)
		if e != nil {
			t.Fatal(e)
		}
		ran, e := worker.ExecuteNext(ctx)
		_ = exclusive.Close()
		if ran || !errors.Is(e, domain.ErrRestoreBusy) || state(t, job.ID).Attempts != 0 {
			t.Fatal("exclusive maintenance spent an attempt", e)
		}
		for _, status := range []string{"queued", "running"} {
			_, e = db.ExecContext(ctx, `INSERT INTO instance_datarestore VALUES ($1,$2,NULL)`, uuid.New(), status)
			if e != nil {
				t.Fatal(e)
			}
			ran, e = worker.ExecuteNext(ctx)
			if ran || !errors.Is(e, domain.ErrRestorePending) || state(t, job.ID).Attempts != 0 {
				t.Fatal("pending maintenance spent an attempt", e)
			}
		}
	})
	t.Run("completed restore rejects stale jobs before effects", func(t *testing.T) {
		defer reset(t)
		job := enqueue(t, 1)
		cutoff := job.CreatedAt.Add(time.Second)
		_, e := db.ExecContext(ctx, `INSERT INTO instance_datarestore VALUES ($1,'completed',$2)`, uuid.New(), cutoff)
		if e != nil {
			t.Fatal(e)
		}
		worker := executor(t, func(context.Context, domain.Job) error { t.Error("stale job executed"); return nil }, time.Second)
		if _, e = worker.ExecuteNext(ctx); e != nil || state(t, job.ID).ErrorCode != "stale_after_restore" {
			t.Fatal("stale job not terminal", e)
		}
		fresh := enqueue(t, 1)
		_, _ = db.ExecContext(ctx, `UPDATE instance_datarestore SET completed_at=$1`, fresh.CreatedAt)
		worker = executor(t, complete, time.Second)
		if _, e = worker.ExecuteNext(ctx); e != nil || state(t, fresh.ID).State != "completed" {
			t.Fatal("equal publication cutoff rejected", e)
		}
	})
	t.Run("shutdown stops intake and drains admitted work", func(t *testing.T) {
		defer reset(t)
		first := enqueue(t, 1)
		second := enqueue(t, 1)
		entered, release := make(chan struct{}), make(chan struct{}, 1)
		defer close(release)
		worker := executor(t, func(ctx context.Context, j domain.Job) error { close(entered); <-release; return complete(ctx, j) }, time.Second)
		stopCtx, stop := context.WithCancel(ctx)
		defer stop()
		done := make(chan error, 1)
		go func() { done <- worker.Run(stopCtx) }()
		await(t, entered)
		stop()
		release <- struct{}{}
		if e := receive(t, done); e != nil {
			t.Fatal(e)
		}
		if state(t, first.ID).State != "completed" || state(t, second.ID).Attempts != 0 {
			t.Fatal("shutdown admitted more work")
		}
	})
	t.Run("drain cancellation keeps restore lock until handler exits", func(t *testing.T) {
		defer reset(t)
		job := enqueue(t, 1)
		entered, cancelled, release := make(chan struct{}), make(chan struct{}), make(chan struct{}, 1)
		defer close(release)
		worker := executor(t, func(ctx context.Context, _ domain.Job) error {
			close(entered)
			<-ctx.Done()
			close(cancelled)
			<-release
			return ctx.Err()
		}, 5*time.Second)
		stopCtx, stop := context.WithCancel(ctx)
		defer stop()
		done := make(chan error, 1)
		go func() { done <- worker.Run(stopCtx) }()
		await(t, entered)
		stop()
		await(t, cancelled)
		lease, e := locks.Acquire(ctx, true, true)
		if lease != nil || !errors.Is(e, domain.ErrRestoreBusy) {
			t.Fatal("shutdown released file lease while handler still runs")
		}
		select {
		case <-done:
			t.Fatal("executor detached an unfinished handler")
		default:
		}
		release <- struct{}{}
		if e = receive(t, done); !errors.Is(e, domain.ErrJobDrainTimeout) {
			t.Fatal("drain timeout lost", e)
		}
		if state(t, job.ID).State != "pending" {
			t.Fatal("interrupted work was discarded")
		}
		lease, e = locks.Acquire(ctx, true, true)
		if e != nil {
			t.Fatal("finished handler leaked restore lock")
		}
		_ = lease.Close()
	})
	t.Run("worker concurrency is bounded and cancellation joins slots", func(t *testing.T) {
		defer reset(t)
		for i := 0; i < 24; i++ {
			enqueue(t, 1)
		}
		var active, peak, finished atomic.Int64
		barrier := make(chan struct{})
		var open sync.Once
		opts := options
		opts.Concurrency = 4
		stopCtx, stop := context.WithCancel(ctx)
		defer stop()
		worker, e := NewJobExecutor(queue, gate, map[string]JobDefinition{"contract.execute": {Version: 1, Timeout: time.Second, Run: func(ctx context.Context, j domain.Job) error {
			n := active.Add(1)
			defer active.Add(-1)
			for {
				old := peak.Load()
				if n <= old || peak.CompareAndSwap(old, n) {
					break
				}
			}
			if n == 4 {
				open.Do(func() { close(barrier) })
			}
			select {
			case <-barrier:
			case <-ctx.Done():
				return ctx.Err()
			}
			time.Sleep(5 * time.Millisecond)
			err := complete(ctx, j)
			if err == nil && finished.Add(1) == 24 {
				stop()
			}
			return err
		}}}, opts)
		if e != nil {
			t.Fatal(e)
		}
		if e = worker.Run(stopCtx); e != nil {
			t.Fatal(e)
		}
		if peak.Load() != 4 || active.Load() != 0 || finished.Load() != 24 {
			t.Fatal("unbounded, detached or lost work")
		}
	})
}
