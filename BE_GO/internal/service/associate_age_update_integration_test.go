//go:build ageupdateintegration

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

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/migrations"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

type ageQueries struct {
	count, active, peak atomic.Int64
	mu                  sync.Mutex
	command             string
	args                []any
}
type ageTraceKey struct{}

func (q *ageQueries) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	q.count.Add(1)
	if strings.HasPrefix(d.SQL, "WITH changed AS") {
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
		ctx = context.WithValue(ctx, ageTraceKey{}, true)
	}
	return ctx
}
func (q *ageQueries) TraceQueryEnd(ctx context.Context, _ *pgx.Conn, _ pgx.TraceQueryEndData) {
	if ctx.Value(ageTraceKey{}) != nil {
		q.active.Add(-1)
	}
}

func TestAssociateAgeUpdatePostgres(t *testing.T) {
	var fixture struct {
		Now  time.Time
		Rows []struct {
			ID      string
			Changed bool
		}
	}
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_AGE_FIXTURE"))
	if err != nil || json.Unmarshal(raw, &fixture) != nil || len(fixture.Rows) != 9 {
		t.Fatal("run accounts.py --only associate-age-update")
	}
	dsn := os.Getenv("ASSOZETA_AGE_DSN")
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	trace := &ageQueries{}
	cfg.Tracer = trace
	db := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	db.SetMaxIdleConns(4)
	if err = migrations.Apply(dsn); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	var ct, jobCount int
	if err = db.GetContext(ctx, &ct, `SELECT id FROM django_content_type WHERE app_label='application' AND model='associate'`); err != nil {
		t.Fatal(err)
	}
	if err = db.GetContext(ctx, &jobCount, `SELECT count(*) FROM assozeta_go_jobs`); err != nil || jobCount != 0 {
		t.Fatal("private jobs not empty")
	}
	var baseline []string
	if err = db.SelectContext(ctx, &baseline, `SELECT to_jsonb(a)::text FROM application_associate a ORDER BY associate_id`); err != nil || len(baseline) != 9 {
		t.Fatal("missing baseline", err)
	}
	// Trusted private fixture handoff; no production ownership switch is exposed.
	if _, err = db.Exec(`UPDATE assozeta_go_job_ownership SET runtime='go',generation=generation+1 WHERE singleton=1`); err != nil {
		t.Fatal(err)
	}
	queue := repository.NewJobs(db)
	var messages bytes.Buffer
	service := NewAssociateAgeUpdate(repository.NewAssociateAgeUpdates(db), func() time.Time { return fixture.Now.In(time.FixedZone("offset", 14*3600)) }, slog.New(slog.NewTextHandler(&messages, nil)))
	exec := func(t *testing.T, query string, args ...any) {
		t.Helper()
		if _, e := db.ExecContext(ctx, query, args...); e != nil {
			t.Fatal(e)
		}
	}
	reset := func(t *testing.T, rows []string) {
		exec(t, `DELETE FROM application_auditlogindex WHERE log_entry_id IN (SELECT id FROM auditlog_logentry WHERE content_type_id=$1)`, ct)
		exec(t, `DELETE FROM auditlog_logentry WHERE content_type_id=$1`, ct)
		exec(t, `DELETE FROM application_associate`)
		exec(t, `DELETE FROM assozeta_go_jobs`)
		for _, row := range rows {
			exec(t, `INSERT INTO application_associate SELECT * FROM jsonb_populate_record(NULL::application_associate,$1::jsonb)`, row)
		}
	}
	enqueue := func(t *testing.T) domain.Job {
		tx, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer tx.Rollback()
		job, e := queue.Enqueue(ctx, tx, domain.JobIntent{ID: uuid.New(), Kind: domain.JobUpdateAssociateAge, Version: 1, Key: uuid.NewString(), Payload: json.RawMessage(`{}`), MaxAttempts: 2})
		if e != nil {
			t.Fatal(e)
		}
		if e = tx.Commit(); e != nil {
			t.Fatal(e)
		}
		return job
	}
	claim := func(t *testing.T) domain.Job {
		enqueue(t)
		jobs, e := queue.Claim(ctx, []string{domain.JobUpdateAssociateAge}, "fixture", 1, time.Minute)
		if e != nil || len(jobs) != 1 {
			t.Fatal("claim", e)
		}
		return jobs[0]
	}
	state := func(t *testing.T, job domain.Job) domain.Job {
		j, e := queue.Get(ctx, uuid.Nil, job.ID)
		if e != nil {
			t.Fatal(e)
		}
		return j
	}
	counts := func(t *testing.T, adults, audits, indexes int) {
		var a, l, i int
		if e := db.GetContext(ctx, &a, `SELECT count(*) FROM application_associate WHERE NOT is_minor`); e != nil {
			t.Fatal(e)
		}
		if e := db.GetContext(ctx, &l, `SELECT count(*) FROM auditlog_logentry WHERE content_type_id=$1`, ct); e != nil {
			t.Fatal(e)
		}
		if e := db.GetContext(ctx, &i, `SELECT count(*) FROM application_auditlogindex i JOIN auditlog_logentry l ON l.id=i.log_entry_id WHERE l.content_type_id=$1`, ct); e != nil {
			t.Fatal(e)
		}
		if a != adults || l != audits || i != indexes {
			t.Fatalf("adults/audits/indexes %d/%d/%d want %d/%d/%d", a, l, i, adults, audits, indexes)
		}
	}
	t.Run("exact age and scope with audit index", func(t *testing.T) {
		reset(t, baseline)
		job := claim(t)
		if e := service.Run(ctx, job); e != nil {
			t.Fatal(e)
		}
		if j := state(t, job); j.State != "completed" || j.Result != nil {
			t.Fatal("None completion changed")
		}
		counts(t, 4, 3, 2)
		for _, row := range fixture.Rows {
			if row.Changed {
				var minor bool
				if e := db.GetContext(ctx, &minor, `SELECT is_minor FROM application_associate WHERE associate_id=$1`, row.ID); e != nil || minor {
					t.Fatal("eligible row not updated", e)
				}
			}
		}
	})
	t.Run("invalid envelope before SQL", func(t *testing.T) {
		reset(t, baseline)
		job := claim(t)
		cases := []domain.Job{job, job, job, job, job, job, job, job}
		cases[0].AssociationID = uuid.New()
		cases[1].Kind = "other"
		cases[2].Version = 2
		cases[3].Payload = json.RawMessage(`null`)
		cases[4].Payload = json.RawMessage(`{"today":"2099-01-01"}`)
		cases[5].LeaseToken = uuid.Nil
		cases[6].ID = uuid.Nil
		cases[7].State = "pending"
		for _, j := range cases {
			trace.count.Store(0)
			if e := service.Run(ctx, j); !errors.Is(e, domain.ErrJobInvalid) || trace.count.Load() != 0 {
				t.Fatal("invalid job reached SQL", e)
			}
		}
		counts(t, 1, 0, 0)
	})
	t.Run("business failures roll back audit and index", func(t *testing.T) {
		for _, table := range []string{"application_associate", "auditlog_logentry", "application_auditlogindex"} {
			reset(t, baseline)
			job := claim(t)
			messages.Reset()
			op := "INSERT"
			if table == "application_associate" {
				op = "UPDATE"
			}
			exec(t, `CREATE FUNCTION contract_age_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'private age failure'; END $$`)
			exec(t, "CREATE TRIGGER contract_age_failure BEFORE "+op+" ON "+table+" FOR EACH STATEMENT EXECUTE FUNCTION contract_age_failure()")
			e := service.Run(ctx, job)
			exec(t, "DROP TRIGGER contract_age_failure ON "+table)
			exec(t, `DROP FUNCTION contract_age_failure()`)
			if e != nil {
				t.Fatal("handled legacy error", e)
			}
			if j := state(t, job); j.State != "completed" || j.Result != nil {
				t.Fatal("handled failure completion")
			}
			counts(t, 1, 0, 0)
			if s := messages.String(); !strings.Contains(s, "associate_age_update_failed") || strings.Contains(s, "private age failure") {
				t.Fatal("unsafe diagnostic")
			}
		}
	})
	t.Run("stale completion and replay cannot change later data", func(t *testing.T) {
		reset(t, baseline)
		job := claim(t)
		exec(t, `UPDATE assozeta_go_jobs SET lease_token=$2 WHERE id=$1`, job.ID, uuid.New())
		if e := service.Run(ctx, job); !errors.Is(e, domain.ErrJobLeaseLost) {
			t.Fatal("stale completion", e)
		}
		counts(t, 1, 0, 0)
		reset(t, baseline)
		job = claim(t)
		if e := service.Run(ctx, job); e != nil {
			t.Fatal(e)
		}
		exec(t, `UPDATE application_associate SET is_minor=TRUE WHERE associate_id=$1`, fixture.Rows[2].ID)
		if e := service.Run(ctx, job); !errors.Is(e, domain.ErrJobLeaseLost) {
			t.Fatal("replay changed later data", e)
		}
		counts(t, 3, 3, 2)
	})
	t.Run("concurrent jobs update and audit once", func(t *testing.T) {
		reset(t, baseline)
		first, second := claim(t), claim(t)
		exec(t, `CREATE FUNCTION contract_age_pause() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(0.15); RETURN NULL; END $$`)
		defer exec(t, `DROP FUNCTION contract_age_pause()`)
		exec(t, `CREATE TRIGGER contract_age_pause BEFORE UPDATE ON application_associate FOR EACH STATEMENT EXECUTE FUNCTION contract_age_pause()`)
		defer exec(t, `DROP TRIGGER contract_age_pause ON application_associate`)
		worker := NewAssociateAgeUpdate(repository.NewAssociateAgeUpdates(db), func() time.Time { return fixture.Now }, slog.New(slog.NewTextHandler(io.Discard, nil)))
		trace.peak.Store(0)
		start := make(chan struct{})
		results := make(chan error, 2)
		for _, job := range []domain.Job{first, second} {
			go func(j domain.Job) { <-start; results <- worker.Run(ctx, j) }(job)
		}
		close(start)
		for range 2 {
			if e := <-results; e != nil {
				t.Fatal(e)
			}
		}
		if trace.peak.Load() != 2 || trace.active.Load() != 0 || state(t, first).State != "completed" || state(t, second).State != "completed" {
			t.Fatal("commands did not overlap and settle")
		}
		counts(t, 4, 3, 2)
	})
	t.Run("blocked SQL cancellation and pool recovery", func(t *testing.T) {
		reset(t, baseline)
		job := claim(t)
		lock, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer lock.Rollback()
		if _, e = lock.ExecContext(ctx, `LOCK TABLE application_associate IN ACCESS EXCLUSIVE MODE`); e != nil {
			t.Fatal(e)
		}
		blocked, stop := context.WithTimeout(ctx, 50*time.Millisecond)
		e = service.Run(blocked, job)
		stop()
		if e == nil {
			t.Fatal("cancelled call succeeded")
		}
		if e = lock.Rollback(); e != nil {
			t.Fatal(e)
		}
		counts(t, 1, 0, 0)
		if state(t, job).State != "running" {
			t.Fatal("recoverable work lost")
		}
		if e = service.Run(ctx, job); e != nil {
			t.Fatal("pool failed to recover", e)
		}
		counts(t, 4, 3, 2)
	})
	t.Run("constant statement count and actual query plan", func(t *testing.T) {
		var template map[string]any
		for _, row := range baseline {
			var parsed map[string]any
			if json.Unmarshal([]byte(row), &parsed) != nil {
				t.Fatal("bad seed")
			}
			if parsed["associate_id"] == fixture.Rows[2].ID {
				template = parsed
			}
		}
		if template == nil {
			t.Fatal("eligible template missing")
		}
		makeRows := func(n, eligible int) []string {
			rows := make([]string, n)
			for i := range rows {
				record := map[string]any{}
				for k, v := range template {
					record[k] = v
				}
				record["associate_id"] = uuid.NewString()
				if i >= eligible {
					record["born_date"] = "2030-03-01"
				}
				raw, e := json.Marshal(record)
				if e != nil {
					t.Fatal(e)
				}
				rows[i] = string(raw)
			}
			return rows
		}
		for _, n := range []int{0, 1, 16, 128, 512} {
			reset(t, makeRows(n, n))
			job := claim(t)
			trace.count.Store(0)
			if e := service.Run(ctx, job); e != nil {
				t.Fatal(e)
			}
			if trace.count.Load() != 5 {
				t.Fatalf("%d rows: %d statements", n, trace.count.Load())
			}
			counts(t, n, n, n)
		}
		reset(t, makeRows(4096, 128))
		trace.mu.Lock()
		command, args := trace.command, append([]any(nil), trace.args...)
		trace.mu.Unlock()
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
			t.Fatal("invalid plan")
		}
		relations := map[string]bool{}
		var visit func(any)
		visit = func(node any) {
			switch n := node.(type) {
			case map[string]any:
				if n["Actual Rows"] == float64(128) {
					if name, ok := n["Relation Name"].(string); ok && (n["Operation"] == "Update" || n["Operation"] == "Insert") {
						relations[name] = true
					}
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
		for _, name := range []string{"application_associate", "auditlog_logentry", "application_auditlogindex"} {
			if !relations[name] {
				t.Fatalf("missing 128-row effect: %s", name)
			}
		}
		if e = os.WriteFile(os.Getenv("ASSOZETA_AGE_PLAN_OUTPUT"), []byte(plan), 0600); e != nil {
			t.Fatal(e)
		}
		counts(t, 0, 0, 0)
	})
	t.Run("heartbeat during update", func(t *testing.T) {
		reset(t, baseline)
		job := enqueue(t)
		exec(t, `CREATE FUNCTION contract_age_pause() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(0.3); RETURN NULL; END $$`)
		defer exec(t, `DROP FUNCTION contract_age_pause()`)
		exec(t, `CREATE TRIGGER contract_age_pause BEFORE UPDATE ON application_associate FOR EACH STATEMENT EXECUTE FUNCTION contract_age_pause()`)
		defer exec(t, `DROP TRIGGER contract_age_pause ON application_associate`)
		gate := NewJobRestoreAdmission(repository.NewRestoreFileLock(""), repository.NewRestoreStates(db), false)
		worker, e := NewJobExecutor(queue, gate, map[string]JobDefinition{domain.JobUpdateAssociateAge: {Version: 1, Timeout: 2 * time.Second, Run: service.Run}}, JobExecutorOptions{Owner: "fixture", Concurrency: 1, Lease: time.Second, RenewInterval: 50 * time.Millisecond, StoreTimeout: 200 * time.Millisecond, PollInterval: time.Millisecond, MaintenanceInterval: time.Second, DrainTimeout: time.Second})
		if e != nil {
			t.Fatal(e)
		}
		if ran, e := worker.ExecuteNext(ctx); e != nil || !ran {
			t.Fatal("executor", e)
		}
		if state(t, job).State != "completed" {
			t.Fatal("heartbeat invalidated completion")
		}
		counts(t, 4, 3, 2)
	})
	reset(t, baseline)
	job := claim(t)
	if err = service.Run(ctx, job); err != nil {
		t.Fatal(err)
	}
	exec(t, `DELETE FROM assozeta_go_jobs`)
}
