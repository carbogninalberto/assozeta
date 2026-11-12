//go:build tokencleanupintegration

package service

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"os"
	"strings"
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
	"github.com/jmoiron/sqlx"
)

type tokenCleanupQueries struct {
	count         atomic.Int64
	active        atomic.Int64
	peak          atomic.Int64
	business      atomic.Int64
	fences        atomic.Int64
	completions   atomic.Int64
	mu            sync.Mutex
	command       string
	args          []any
	afterRollback func()
	afterBusiness func()
}

func (q *tokenCleanupQueries) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	q.count.Add(1)
	ctx = context.WithValue(ctx, cleanupSQLTraceKey{}, d.SQL)
	if strings.Contains(d.SQL, "SELECT o.generation FROM ownership o JOIN") {
		q.fences.Add(1)
	}
	if strings.Contains(d.SQL, "UPDATE public.assozeta_go_jobs j SET state='completed'") {
		q.completions.Add(1)
	}
	if strings.HasPrefix(d.SQL, "WITH removed AS") {
		q.business.Add(1)
		q.mu.Lock()
		q.command = d.SQL
		q.args = append([]any(nil), d.Args...)
		q.mu.Unlock()
		n := q.active.Add(1)
		for old := q.peak.Load(); n > old; old = q.peak.Load() {
			if q.peak.CompareAndSwap(old, n) {
				break
			}
		}
		ctx = context.WithValue(ctx, cleanupTraceKey{}, true)
	}
	return ctx
}

type cleanupTraceKey struct{}
type cleanupSQLTraceKey struct{}

func (q *tokenCleanupQueries) TraceQueryEnd(ctx context.Context, _ *pgx.Conn, data pgx.TraceQueryEndData) {
	q.mu.Lock()
	afterRollback := q.afterRollback
	afterBusiness := q.afterBusiness
	q.mu.Unlock()
	if ctx.Value(cleanupTraceKey{}) != nil {
		q.active.Add(-1)
	}
	sql, _ := ctx.Value(cleanupSQLTraceKey{}).(string)
	if strings.HasPrefix(sql, "WITH removed AS") && data.Err == nil && afterBusiness != nil {
		afterBusiness()
	}
	if strings.EqualFold(sql, "rollback") && data.Err == nil && afterRollback != nil {
		afterRollback()
	}
}

type cleanupFixtureToken struct {
	ID, Subscription, Token string
	Created, Expires        time.Time
	Deleted                 bool
}

func TestSubscriptionTokenCleanupPostgres(t *testing.T) {
	var fixture struct {
		Now, Cutoff     time.Time
		Tokens          []cleanupFixtureToken
		ExpectedChanges map[string]map[string][2]string `json:"expected_changes"`
	}
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_TOKEN_CLEANUP_FIXTURE"))
	if err != nil {
		t.Fatal("run accounts.py --only subscription-token-cleanup")
	}
	if json.Unmarshal(raw, &fixture) != nil || len(fixture.Tokens) != 7 {
		t.Fatal("missing explicit token fixture")
	}
	dsn := os.Getenv("ASSOZETA_TOKEN_CLEANUP_DSN")
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	trace := &tokenCleanupQueries{}
	cfg.Tracer = trace
	db := sqlx.NewDb(ownedpgx.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	db.SetMaxIdleConns(4)
	if err = migrations.Apply(dsn); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	var ct int
	if err = db.GetContext(ctx, &ct, `SELECT id FROM django_content_type WHERE app_label='application' AND model='subscriptiontoken'`); err != nil {
		t.Fatal(err)
	}
	var jobCount int
	if err = db.GetContext(ctx, &jobCount, `SELECT count(*) FROM assozeta_go_jobs`); err != nil || jobCount != 0 {
		t.Fatal("private job table is not empty")
	}
	// Trusted private fixture handoff; no production ownership switch is exposed.
	if _, err = db.Exec(`UPDATE assozeta_go_job_ownership SET runtime='go',generation=generation+1 WHERE singleton=1`); err != nil {
		t.Fatal(err)
	}
	queue := repository.NewJobs(db)
	var messages bytes.Buffer
	// +14 changes the civil day, but this is still the same UTC execution instant.
	cleanup := NewSubscriptionTokenCleanup(repository.NewSubscriptionTokenCleanups(db), func() time.Time { return fixture.Now.In(time.FixedZone("offset", 14*3600)) }, slog.New(slog.NewTextHandler(&messages, nil)))
	exec := func(t *testing.T, query string, args ...any) {
		t.Helper()
		if _, e := db.ExecContext(ctx, query, args...); e != nil {
			t.Fatal(e)
		}
	}
	seed := func(t *testing.T, rows []cleanupFixtureToken) {
		t.Helper()
		exec(t, `DELETE FROM application_subscriptiontoken`)
		exec(t, `DELETE FROM auditlog_logentry WHERE content_type_id=$1`, ct)
		exec(t, `DELETE FROM assozeta_go_jobs`)
		for _, row := range rows {
			exec(t, `INSERT INTO application_subscriptiontoken(subscription_token_id,subscription_id,token,creation_date,expiration_date) VALUES($1,$2,$3,$4,$5)`, row.ID, row.Subscription, row.Token, row.Created, row.Expires)
		}
	}
	enqueue := func(t *testing.T, key string) domain.Job {
		t.Helper()
		tx, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer tx.Rollback()
		job, e := queue.Enqueue(ctx, tx, domain.JobIntent{ID: uuid.New(), Kind: domain.JobClearExpiredSubscriptionTokens, Version: 1, Key: key, Payload: json.RawMessage(`{}`), MaxAttempts: 2})
		if e != nil {
			t.Fatal(e)
		}
		if e = tx.Commit(); e != nil {
			t.Fatal(e)
		}
		return job
	}
	claim := func(t *testing.T) domain.Job {
		t.Helper()
		enqueue(t, uuid.NewString())
		jobs, e := queue.Claim(ctx, []string{domain.JobClearExpiredSubscriptionTokens}, "fixture", 1, time.Minute)
		if e != nil || len(jobs) != 1 {
			t.Fatal("claim", e)
		}
		return jobs[0]
	}
	state := func(t *testing.T, job domain.Job) domain.Job {
		t.Helper()
		j, e := queue.Get(ctx, uuid.Nil, job.ID)
		if e != nil {
			t.Fatal(e)
		}
		return j
	}
	counts := func(t *testing.T, tokens, audits int) {
		t.Helper()
		var gotTokens, gotAudits, indexes int
		if e := db.GetContext(ctx, &gotTokens, `SELECT count(*) FROM application_subscriptiontoken`); e != nil {
			t.Fatal(e)
		}
		if e := db.GetContext(ctx, &gotAudits, `SELECT count(*) FROM auditlog_logentry WHERE content_type_id=$1`, ct); e != nil {
			t.Fatal(e)
		}
		if e := db.GetContext(ctx, &indexes, `SELECT count(*) FROM application_auditlogindex i JOIN auditlog_logentry l ON l.id=i.log_entry_id WHERE l.content_type_id=$1`, ct); e != nil {
			t.Fatal(e)
		}
		if gotTokens != tokens || gotAudits != audits || indexes != 0 {
			t.Fatalf("tokens/audits/indexes=%d/%d/%d, want %d/%d/0", gotTokens, gotAudits, indexes, tokens, audits)
		}
	}
	t.Run("UTC cutoff exact audit and deleted association scope", func(t *testing.T) {
		seed(t, fixture.Tokens)
		job := claim(t)
		if e := cleanup.Run(ctx, job); e != nil {
			t.Fatal(e)
		}
		got := state(t, job)
		if got.State != "completed" || got.Result != nil {
			t.Fatal("legacy None completion changed")
		}
		counts(t, 4, 3)
		var entries []struct {
			ID      string          `db:"object_pk"`
			Changes json.RawMessage `db:"changes"`
		}
		if e := db.SelectContext(ctx, &entries, `SELECT object_pk,changes FROM auditlog_logentry WHERE content_type_id=$1`, ct); e != nil {
			t.Fatal(e)
		}
		for _, entry := range entries {
			var changes map[string][2]string
			if json.Unmarshal(entry.Changes, &changes) != nil {
				t.Fatal("invalid audit JSON")
			}
			expected := fixture.ExpectedChanges[entry.ID]
			if len(changes) != len(expected) {
				t.Fatal("audit fields changed")
			}
			for k, v := range expected {
				if changes[k] != v {
					t.Fatalf("audit %s mismatch", k)
				}
			}
		}
	})
	t.Run("invalid job scope and arguments reject before SQL", func(t *testing.T) {
		seed(t, fixture.Tokens)
		job := claim(t)
		cases := []domain.Job{job, job, job, job, job, job}
		cases[0].AssociationID = uuid.New()
		cases[1].Payload = json.RawMessage(`null`)
		cases[2].Payload = json.RawMessage(`{"cutoff":"2099-01-01"}`)
		cases[3].Kind = "other"
		cases[4].Version = 2
		cases[5].LeaseToken = uuid.Nil
		for _, j := range cases {
			trace.count.Store(0)
			if e := cleanup.Run(ctx, j); !errors.Is(e, domain.ErrJobInvalid) {
				t.Fatal("invalid job accepted", e)
			}
			if trace.count.Load() != 0 {
				t.Fatal("invalid job touched SQL")
			}
		}
		counts(t, 7, 0)
	})
	t.Run("audit failure rolls back every deletion", func(t *testing.T) {
		seed(t, fixture.Tokens)
		job := claim(t)
		messages.Reset()
		exec(t, `CREATE FUNCTION contract_token_audit_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'private token audit failure'; END $$`)
		defer exec(t, `DROP FUNCTION contract_token_audit_failure()`)
		exec(t, `CREATE TRIGGER contract_token_audit_failure BEFORE INSERT ON auditlog_logentry FOR EACH STATEMENT EXECUTE FUNCTION contract_token_audit_failure()`)
		defer exec(t, `DROP TRIGGER contract_token_audit_failure ON auditlog_logentry`)
		if e := cleanup.Run(ctx, job); e != nil {
			t.Fatal("legacy caught SQL error", e)
		}
		if got := state(t, job); got.State != "completed" || got.Result != nil {
			t.Fatal("handled error result changed")
		}
		counts(t, 7, 0)
		if output := messages.String(); !strings.Contains(output, "subscription_token_cleanup_failed") || strings.Contains(output, "private token audit failure") {
			t.Fatal("unsafe/missing diagnostic")
		}
	})
	t.Run("stale ownership generation expired lease and token send no business SQL", func(t *testing.T) {
		for _, defect := range []string{"token", "expired", "generation", "django"} {
			seed(t, fixture.Tokens)
			job := claim(t)
			switch defect {
			case "token":
				exec(t, `UPDATE assozeta_go_jobs SET lease_token=$2 WHERE id=$1`, job.ID, uuid.New())
			case "expired":
				exec(t, `UPDATE assozeta_go_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=$1`, job.ID)
			case "generation":
				exec(t, `UPDATE assozeta_go_job_ownership SET generation=generation+1 WHERE singleton=1`)
			case "django":
				exec(t, `UPDATE assozeta_go_job_ownership SET runtime='django',generation=generation+1 WHERE singleton=1`)
			}
			trace.business.Store(0)
			err := cleanup.Run(ctx, job)
			// Fixture-only restoration; no production transition API is involved.
			exec(t, `UPDATE assozeta_go_job_ownership SET runtime='go' WHERE singleton=1`)
			if !errors.Is(err, domain.ErrJobLeaseLost) || trace.business.Load() != 0 {
				t.Errorf("%s: stale admission sent %d business commands, error %v", defect, trace.business.Load(), err)
			}
			counts(t, 7, 0)
			if state(t, job).State != "running" {
				t.Fatal("rejected attempt changed job state")
			}
		}
	})
	t.Run("handled failure fences completion-only transaction", func(t *testing.T) {
		for _, loss := range []string{"none", "owner", "token"} {
			seed(t, fixture.Tokens)
			job := claim(t)
			exec(t, `CREATE FUNCTION contract_token_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'private token failure'; END $$`)
			exec(t, `CREATE TRIGGER contract_token_failure BEFORE INSERT ON auditlog_logentry FOR EACH STATEMENT EXECUTE FUNCTION contract_token_failure()`)
			trace.fences.Store(0)
			trace.completions.Store(0)
			var changed atomic.Bool
			var transitionErr error
			trace.mu.Lock()
			trace.afterRollback = func() {
				if loss != "none" && changed.CompareAndSwap(false, true) {
					if loss == "owner" {
						_, transitionErr = db.ExecContext(ctx, `UPDATE assozeta_go_job_ownership SET runtime='django',generation=generation+1 WHERE singleton=1`)
					} else {
						_, transitionErr = db.ExecContext(ctx, `UPDATE assozeta_go_jobs SET lease_token=$2 WHERE id=$1`, job.ID, uuid.New())
					}
				}
			}
			trace.mu.Unlock()
			err := cleanup.Run(ctx, job)
			trace.mu.Lock()
			trace.afterRollback = nil
			trace.mu.Unlock()
			exec(t, `DROP TRIGGER contract_token_failure ON auditlog_logentry`)
			exec(t, `DROP FUNCTION contract_token_failure()`)
			exec(t, `UPDATE assozeta_go_job_ownership SET runtime='go' WHERE singleton=1`)
			if transitionErr != nil || trace.fences.Load() != 2 {
				t.Errorf("both transactions require effect admission: fences=%d transition=%v", trace.fences.Load(), transitionErr)
			}
			if loss != "none" {
				if !changed.Load() || !errors.Is(err, domain.ErrJobLeaseLost) || trace.completions.Load() != 0 || state(t, job).State != "running" {
					t.Errorf("completion-only transaction ignored ownership loss: %v, completions=%d", err, trace.completions.Load())
				}
			} else if err != nil || trace.completions.Load() != 1 || state(t, job).State != "completed" {
				t.Errorf("handled failure did not complete once: %v", err)
			}
			counts(t, 7, 0)
		}
	})
	t.Run("stale completion rolls back tokens and audits", func(t *testing.T) {
		for _, loss := range []string{"token", "expired"} {
			seed(t, fixture.Tokens)
			job := claim(t)
			trace.business.Store(0)
			trace.completions.Store(0)
			var transitionErr error
			var changed atomic.Bool
			trace.mu.Lock()
			trace.afterBusiness = func() {
				// The real DELETE/audit command has succeeded inside the open
				// transaction. Lose the lease before completion, not admission.
				if changed.CompareAndSwap(false, true) {
					if loss == "token" {
						_, transitionErr = db.ExecContext(ctx, `UPDATE assozeta_go_jobs SET lease_token=$2 WHERE id=$1`, job.ID, uuid.New())
					} else {
						_, transitionErr = db.ExecContext(ctx, `UPDATE assozeta_go_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=$1`, job.ID)
					}
				}
			}
			trace.mu.Unlock()
			err := cleanup.Run(ctx, job)
			trace.mu.Lock()
			trace.afterBusiness = nil
			trace.mu.Unlock()
			if transitionErr != nil || !changed.Load() || trace.business.Load() != 1 || trace.completions.Load() != 1 {
				t.Fatalf("%s: completion race not exercised: business=%d completion=%d transition=%v", loss, trace.business.Load(), trace.completions.Load(), transitionErr)
			}
			if !errors.Is(err, domain.ErrJobLeaseLost) || state(t, job).State != "running" {
				t.Fatalf("%s: stale attempt committed: %v", loss, err)
			}
			counts(t, 7, 0)
		}
	})
	t.Run("replay cannot delete a later token or duplicate audits", func(t *testing.T) {
		seed(t, fixture.Tokens)
		job := claim(t)
		if e := cleanup.Run(ctx, job); e != nil {
			t.Fatal(e)
		}
		row := fixture.Tokens[0]
		row.ID = uuid.NewString()
		exec(t, `INSERT INTO application_subscriptiontoken(subscription_token_id,subscription_id,token,creation_date,expiration_date) VALUES($1,$2,$3,$4,$5)`, row.ID, row.Subscription, row.Token, row.Created, row.Expires)
		if e := cleanup.Run(ctx, job); !errors.Is(e, domain.ErrJobLeaseLost) {
			t.Fatal("completed attempt replay accepted", e)
		}
		counts(t, 5, 3)
		duplicate := enqueue(t, job.Key)
		if duplicate.ID != job.ID || duplicate.State != "completed" {
			t.Fatal("completed occurrence reopened")
		}
	})
	t.Run("concurrent cleanup audits each deleted token once", func(t *testing.T) {
		seed(t, fixture.Tokens)
		first, second := claim(t), claim(t)
		exec(t, `CREATE FUNCTION contract_token_concurrent_pause() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(0.15); RETURN NULL; END $$`)
		defer exec(t, `DROP FUNCTION contract_token_concurrent_pause()`)
		exec(t, `CREATE TRIGGER contract_token_concurrent_pause BEFORE DELETE ON application_subscriptiontoken FOR EACH STATEMENT EXECUTE FUNCTION contract_token_concurrent_pause()`)
		defer exec(t, `DROP TRIGGER contract_token_concurrent_pause ON application_subscriptiontoken`)
		trace.peak.Store(0)
		var wg sync.WaitGroup
		results := make(chan error, 2)
		start := make(chan struct{})
		// A concurrent logger must not share the test's unsynchronized bytes.Buffer.
		worker := NewSubscriptionTokenCleanup(repository.NewSubscriptionTokenCleanups(db), func() time.Time { return fixture.Now }, slog.New(slog.NewTextHandler(io.Discard, nil)))
		for _, job := range []domain.Job{first, second} {
			wg.Add(1)
			go func(j domain.Job) { defer wg.Done(); <-start; results <- worker.Run(ctx, j) }(job)
		}
		close(start)
		wg.Wait()
		close(results)
		for e := range results {
			if e != nil {
				t.Fatal(e)
			}
		}
		if state(t, first).State != "completed" || state(t, second).State != "completed" {
			t.Fatal("cleanup jobs did not settle")
		}
		counts(t, 4, 3)
		if trace.peak.Load() != 2 || trace.active.Load() != 0 {
			t.Fatal("two cleanup commands did not overlap and settle")
		}
	})
	t.Run("blocked read cancellation preserves recoverable work", func(t *testing.T) {
		seed(t, fixture.Tokens)
		job := claim(t)
		lock, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer lock.Rollback()
		if _, e = lock.ExecContext(ctx, `LOCK TABLE application_subscriptiontoken IN ACCESS EXCLUSIVE MODE`); e != nil {
			t.Fatal(e)
		}
		timeout, stop := context.WithTimeout(ctx, 50*time.Millisecond)
		e = cleanup.Run(timeout, job)
		stop()
		if e == nil {
			t.Fatal("cancelled cleanup reported success")
		}
		if e = lock.Rollback(); e != nil {
			t.Fatal(e)
		}
		if state(t, job).State != "running" {
			t.Fatal("cancelled work lost recoverable lease")
		}
		counts(t, 7, 0)
		if e = cleanup.Run(ctx, job); e != nil {
			t.Fatal("pool did not recover", e)
		}
		counts(t, 4, 3)
	})
	t.Run("single command delete and audit across increasing sizes", func(t *testing.T) {
		for _, n := range []int{0, 1, 16, 128, 129, 257, 512} {
			rows := make([]cleanupFixtureToken, n)
			for i := range rows {
				rows[i] = fixture.Tokens[0]
				rows[i].ID = uuid.NewString()
			}
			seed(t, rows)
			job := claim(t)
			trace.count.Store(0)
			if e := cleanup.Run(ctx, job); e != nil {
				t.Fatal(e)
			}
			want := int64(6)
			if got := trace.count.Load(); got != want {
				t.Fatalf("%d tokens: %d statements, want %d", n, got, want)
			}
			counts(t, 0, n)
		}
		t.Log("Six statements: BEGIN, shared server fence, early ownership/lease admission, DELETE RETURNING with audit INSERT, fenced completion and COMMIT; no token rows materialized in Go")
	})
	t.Run("heartbeat can renew during atomic cleanup", func(t *testing.T) {
		seed(t, fixture.Tokens)
		job := enqueue(t, uuid.NewString())
		exec(t, `CREATE FUNCTION contract_token_slow_delete() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(0.3); RETURN NULL; END $$`)
		defer exec(t, `DROP FUNCTION contract_token_slow_delete()`)
		exec(t, `CREATE TRIGGER contract_token_slow_delete BEFORE DELETE ON application_subscriptiontoken FOR EACH STATEMENT EXECUTE FUNCTION contract_token_slow_delete()`)
		defer exec(t, `DROP TRIGGER contract_token_slow_delete ON application_subscriptiontoken`)
		gate := NewJobRestoreAdmission(repository.NewRestoreFileLock(""), repository.NewRestoreStates(db), false)
		worker, e := NewJobExecutor(queue, gate, map[string]JobDefinition{domain.JobClearExpiredSubscriptionTokens: {Version: 1, Timeout: 2 * time.Second, Run: cleanup.Run}}, JobExecutorOptions{Owner: "fixture", Concurrency: 1, Lease: time.Second, RenewInterval: 50 * time.Millisecond, StoreTimeout: 200 * time.Millisecond, PollInterval: time.Millisecond, MaintenanceInterval: time.Second, DrainTimeout: time.Second})
		if e != nil {
			t.Fatal(e)
		}
		if ran, e := worker.ExecuteNext(ctx); e != nil || !ran {
			t.Fatal("long cleanup did not execute", e)
		}
		if got := state(t, job); got.State != "completed" {
			t.Fatalf("heartbeat invalidated business transaction: %s/%s", got.State, got.ErrorCode)
		}
		counts(t, 4, 3)
	})
	t.Run("actual delete and audit query plan", func(t *testing.T) {
		rows := make([]cleanupFixtureToken, 4096)
		for i := range rows {
			rows[i] = fixture.Tokens[0]
			rows[i].ID = uuid.NewString()
			if i >= 128 {
				rows[i].Expires = fixture.Cutoff.Add(24 * time.Hour)
			}
		}
		seed(t, rows)
		trace.mu.Lock()
		command, args := trace.command, append([]any(nil), trace.args...)
		trace.mu.Unlock()
		if command == "" {
			t.Fatal("actual cleanup SQL was not captured")
		}
		tx, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer tx.Rollback()
		var plan string
		if e = tx.GetContext(ctx, &plan, "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) "+command, args...); e != nil {
			t.Fatal(e)
		}
		if e = tx.Rollback(); e != nil {
			t.Fatal(e)
		}
		var decoded any
		if json.Unmarshal([]byte(plan), &decoded) != nil {
			t.Fatal("invalid PostgreSQL plan")
		}
		operations := map[string]bool{}
		var visit func(any)
		visit = func(node any) {
			switch n := node.(type) {
			case map[string]any:
				if operation, ok := n["Operation"].(string); ok && (operation == "Delete" || operation == "Insert") && n["Actual Rows"] == float64(128) {
					operations[operation] = true
				}
				for _, child := range n {
					visit(child)
				}
			case []any:
				for _, child := range n {
					visit(child)
				}
			}
		}
		visit(decoded)
		if !operations["Delete"] || !operations["Insert"] {
			t.Fatal("plan did not delete and audit 128 selected rows")
		}
		if e = os.WriteFile(os.Getenv("ASSOZETA_TOKEN_PLAN_OUTPUT"), []byte(plan), 0600); e != nil {
			t.Fatal(e)
		}
		counts(t, 4096, 0)
	})
	// Leave the exact declared baseline outcome for independent Django readback.
	seed(t, fixture.Tokens)
	job := claim(t)
	if err = cleanup.Run(ctx, job); err != nil {
		t.Fatal(err)
	}
	exec(t, `DELETE FROM assozeta_go_jobs`)
}
