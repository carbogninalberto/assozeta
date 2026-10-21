//go:build auditretentionintegration

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
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

type retentionQueries struct {
	count, active, peak atomic.Int64
	mu                  sync.Mutex
	command             string
	args                []any
}
type retentionTraceKey struct{}

func (q *retentionQueries) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	q.count.Add(1)
	if strings.HasPrefix(d.SQL, "WITH expired_audits AS") {
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
		ctx = context.WithValue(ctx, retentionTraceKey{}, true)
	}
	return ctx
}
func (q *retentionQueries) TraceQueryEnd(ctx context.Context, _ *pgx.Conn, _ pgx.TraceQueryEndData) {
	if ctx.Value(retentionTraceKey{}) != nil {
		q.active.Add(-1)
	}
}

func TestAuditRetentionPostgres(t *testing.T) {
	var fixture struct {
		Now, Cutoff time.Time
		Rows        []struct {
			ID               int
			Expired, Indexed bool
		}
	}
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_AUDIT_FIXTURE"))
	if err != nil || json.Unmarshal(raw, &fixture) != nil || len(fixture.Rows) != 7 {
		t.Fatal("run accounts.py --only audit-retention")
	}
	dsn := os.Getenv("ASSOZETA_AUDIT_DSN")
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	trace := &retentionQueries{}
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
	var jobCount int
	if err = db.GetContext(ctx, &jobCount, `SELECT count(*) FROM assozeta_go_jobs`); err != nil || jobCount != 0 {
		t.Fatal("private jobs not empty")
	}
	var baseLogs, baseIndexes []string
	if err = db.SelectContext(ctx, &baseLogs, `SELECT to_jsonb(l)::text FROM auditlog_logentry l ORDER BY id`); err != nil || len(baseLogs) != 7 {
		t.Fatal("missing logs", err)
	}
	if err = db.SelectContext(ctx, &baseIndexes, `SELECT to_jsonb(i)::text FROM application_auditlogindex i ORDER BY log_entry_id`); err != nil || len(baseIndexes) != 4 {
		t.Fatal("missing indexes", err)
	}
	queue := repository.NewJobs(db)
	var messages bytes.Buffer
	service := NewAuditRetention(repository.NewAuditRetentions(db), func() time.Time { return fixture.Now.In(time.FixedZone("offset", 14*3600)) }, slog.New(slog.NewTextHandler(&messages, nil)))
	exec := func(t *testing.T, query string, args ...any) {
		t.Helper()
		if _, e := db.ExecContext(ctx, query, args...); e != nil {
			t.Fatal(e)
		}
	}
	insertLog := func(t *testing.T, row string) {
		exec(t, `INSERT INTO auditlog_logentry SELECT * FROM jsonb_populate_record(NULL::auditlog_logentry,$1::jsonb)`, row)
	}
	insertIndex := func(t *testing.T, row string) {
		exec(t, `INSERT INTO application_auditlogindex SELECT * FROM jsonb_populate_record(NULL::application_auditlogindex,$1::jsonb)`, row)
	}
	reset := func(t *testing.T, logs, indexes []string) {
		exec(t, `DELETE FROM application_auditlogindex`)
		exec(t, `DELETE FROM auditlog_logentry`)
		exec(t, `DELETE FROM assozeta_go_jobs`)
		for _, row := range logs {
			insertLog(t, row)
		}
		for _, row := range indexes {
			insertIndex(t, row)
		}
	}
	enqueue := func(t *testing.T) domain.Job {
		tx, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer tx.Rollback()
		job, e := queue.Enqueue(ctx, tx, domain.JobIntent{ID: uuid.New(), Kind: domain.JobDeleteOldAuditLogs, Version: 1, Key: uuid.NewString(), Payload: json.RawMessage(`{}`), MaxAttempts: 2})
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
		jobs, e := queue.Claim(ctx, []string{domain.JobDeleteOldAuditLogs}, "fixture", 1, time.Minute)
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
	counts := func(t *testing.T, logs, indexes int) {
		t.Helper()
		var l, i int
		if e := db.GetContext(ctx, &l, `SELECT count(*) FROM auditlog_logentry`); e != nil {
			t.Fatal(e)
		}
		if e := db.GetContext(ctx, &i, `SELECT count(*) FROM application_auditlogindex`); e != nil {
			t.Fatal(e)
		}
		if l != logs || i != indexes {
			t.Fatalf("logs/indexes %d/%d want %d/%d", l, i, logs, indexes)
		}
	}
	clone := func(t *testing.T, raw string, changes map[string]any) string {
		t.Helper()
		var row map[string]any
		if json.Unmarshal([]byte(raw), &row) != nil {
			t.Fatal("invalid seed")
		}
		for k, v := range changes {
			row[k] = v
		}
		b, e := json.Marshal(row)
		if e != nil {
			t.Fatal(e)
		}
		return string(b)
	}
	t.Run("exact cutoff and dependent index cleanup", func(t *testing.T) {
		reset(t, baseLogs, baseIndexes)
		job := claim(t)
		if e := service.Run(ctx, job); e != nil {
			t.Fatal(e)
		}
		if j := state(t, job); j.State != "completed" || j.Result != nil {
			t.Fatal("None completion changed")
		}
		counts(t, 4, 2)
		for _, row := range fixture.Rows {
			var found bool
			if e := db.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM auditlog_logentry WHERE id=$1)`, row.ID); e != nil || found == row.Expired {
				t.Fatal("cutoff selection", e)
			}
		}
		if !strings.Contains(messages.String(), "records=5") {
			t.Fatal("cascade count must include three logs and two indexes")
		}
	})
	t.Run("invalid envelope before SQL", func(t *testing.T) {
		reset(t, baseLogs, baseIndexes)
		job := claim(t)
		cases := []domain.Job{job, job, job, job, job, job, job, job}
		cases[0].AssociationID = uuid.New()
		cases[1].Kind = "other"
		cases[2].Version = 2
		cases[3].Payload = json.RawMessage(`null`)
		cases[4].Payload = json.RawMessage(`{"days":0}`)
		cases[5].LeaseToken = uuid.Nil
		cases[6].ID = uuid.Nil
		cases[7].State = "pending"
		for _, j := range cases {
			trace.count.Store(0)
			if e := service.Run(ctx, j); !errors.Is(e, domain.ErrJobInvalid) || trace.count.Load() != 0 {
				t.Fatal("invalid job reached SQL", e)
			}
		}
		counts(t, 7, 4)
	})
	t.Run("both deletion failures roll back", func(t *testing.T) {
		for _, table := range []string{"auditlog_logentry", "application_auditlogindex"} {
			reset(t, baseLogs, baseIndexes)
			job := claim(t)
			messages.Reset()
			exec(t, `CREATE FUNCTION contract_retention_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'private retention failure'; END $$`)
			exec(t, "CREATE TRIGGER contract_retention_failure BEFORE DELETE ON "+table+" FOR EACH STATEMENT EXECUTE FUNCTION contract_retention_failure()")
			e := service.Run(ctx, job)
			exec(t, "DROP TRIGGER contract_retention_failure ON "+table)
			exec(t, `DROP FUNCTION contract_retention_failure()`)
			if e != nil {
				t.Fatal("handled error", e)
			}
			if j := state(t, job); j.State != "completed" || j.Result != nil {
				t.Fatal("handled completion")
			}
			counts(t, 7, 4)
			if s := messages.String(); !strings.Contains(s, "audit_retention_failed") || strings.Contains(s, "private retention failure") {
				t.Fatal("unsafe diagnostic")
			}
		}
	})
	t.Run("stale completion and replay preserve later records", func(t *testing.T) {
		reset(t, baseLogs, baseIndexes)
		job := claim(t)
		exec(t, `UPDATE assozeta_go_jobs SET lease_token=$2 WHERE id=$1`, job.ID, uuid.New())
		if e := service.Run(ctx, job); !errors.Is(e, domain.ErrJobLeaseLost) {
			t.Fatal("stale completion", e)
		}
		counts(t, 7, 4)
		reset(t, baseLogs, baseIndexes)
		job = claim(t)
		if e := service.Run(ctx, job); e != nil {
			t.Fatal(e)
		}
		insertLog(t, clone(t, baseLogs[0], map[string]any{"id": 999999}))
		insertIndex(t, clone(t, baseIndexes[0], map[string]any{"audit_log_index_id": uuid.NewString(), "log_entry_id": 999999}))
		if e := service.Run(ctx, job); !errors.Is(e, domain.ErrJobLeaseLost) {
			t.Fatal("replay deleted later records", e)
		}
		counts(t, 5, 3)
	})
	t.Run("concurrent retention settles once", func(t *testing.T) {
		reset(t, baseLogs, baseIndexes)
		first, second := claim(t), claim(t)
		exec(t, `CREATE FUNCTION contract_retention_pause() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(0.15); RETURN NULL; END $$`)
		defer exec(t, `DROP FUNCTION contract_retention_pause()`)
		exec(t, `CREATE TRIGGER contract_retention_pause BEFORE DELETE ON auditlog_logentry FOR EACH STATEMENT EXECUTE FUNCTION contract_retention_pause()`)
		defer exec(t, `DROP TRIGGER contract_retention_pause ON auditlog_logentry`)
		worker := NewAuditRetention(repository.NewAuditRetentions(db), func() time.Time { return fixture.Now }, slog.New(slog.NewTextHandler(io.Discard, nil)))
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
		counts(t, 4, 2)
	})
	t.Run("concurrent index insertion cannot leave an orphan", func(t *testing.T) {
		reset(t, baseLogs, baseIndexes)
		job := claim(t)
		writer, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer writer.Rollback()
		// The second old log has no index. Hold its parent-key lock until a
		// newly inserted index commits, after retention takes its snapshot.
		var id int
		if e = writer.GetContext(ctx, &id, `SELECT id FROM auditlog_logentry WHERE id=$1 FOR KEY SHARE`, fixture.Rows[1].ID); e != nil {
			t.Fatal(e)
		}
		row := clone(t, baseIndexes[0], map[string]any{"audit_log_index_id": uuid.NewString(), "log_entry_id": id})
		if _, e = writer.ExecContext(ctx, `INSERT INTO application_auditlogindex SELECT * FROM jsonb_populate_record(NULL::application_auditlogindex,$1::jsonb)`, row); e != nil {
			t.Fatal(e)
		}
		runCtx, stop := context.WithTimeout(ctx, 5*time.Second)
		result := make(chan error, 1)
		finished := false
		defer func() {
			stop()
			writer.Rollback()
			if !finished {
				<-result
			}
		}()
		go func() { result <- service.Run(runCtx, job) }()
		deadline := time.Now().Add(2 * time.Second)
		for {
			var waiting bool
			if e = db.GetContext(ctx, &waiting, `SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE 'WITH expired_audits AS%')`); e != nil {
				t.Fatal(e)
			}
			if waiting {
				break
			}
			if time.Now().After(deadline) {
				t.Fatal("retention did not wait on the index writer")
			}
			time.Sleep(5 * time.Millisecond)
		}
		if e = writer.Commit(); e != nil {
			t.Fatal(e)
		}
		e = <-result
		finished = true
		if e != nil {
			t.Fatalf("Django handles the deferred FK failure as None: %v", e)
		}
		counts(t, 7, 5)
		if j := state(t, job); j.State != "completed" || j.Result != nil {
			t.Fatal("handled commit failure did not complete with NULL")
		}
		if e = service.Run(ctx, job); !errors.Is(e, domain.ErrJobLeaseLost) {
			t.Fatal("completed occurrence reopened", e)
		}
		counts(t, 7, 5)
		// A later scheduled occurrence sees the new child and completes retention.
		next := claim(t)
		if e = service.Run(ctx, next); e != nil {
			t.Fatal("next occurrence after child race", e)
		}
		counts(t, 4, 2)
	})
	t.Run("unrelated commit failures are not swallowed", func(t *testing.T) {
		reset(t, baseLogs, baseIndexes)
		job := claim(t)
		exec(t, `CREATE FUNCTION contract_retention_commit_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='private unexpected constraint'; END $$`)
		defer exec(t, `DROP FUNCTION contract_retention_commit_failure()`)
		exec(t, `CREATE CONSTRAINT TRIGGER contract_retention_commit_failure AFTER DELETE ON auditlog_logentry DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION contract_retention_commit_failure()`)
		e := service.Run(ctx, job)
		exec(t, `DROP TRIGGER contract_retention_commit_failure ON auditlog_logentry`)
		var constraint *pgconn.PgError
		if !errors.As(e, &constraint) || constraint.Code != "23514" {
			t.Fatalf("unexpected commit failure was swallowed: %v", e)
		}
		counts(t, 7, 4)
		if state(t, job).State != "running" {
			t.Fatal("failed commit persisted completion")
		}
		if e = service.Run(ctx, job); e != nil {
			t.Fatal("commit failure left unusable pool", e)
		}
		counts(t, 4, 2)
	})
	t.Run("blocked SQL cancellation and pool recovery", func(t *testing.T) {
		reset(t, baseLogs, baseIndexes)
		job := claim(t)
		lock, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer lock.Rollback()
		if _, e = lock.ExecContext(ctx, `LOCK TABLE auditlog_logentry IN ACCESS EXCLUSIVE MODE`); e != nil {
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
		counts(t, 7, 4)
		if state(t, job).State != "running" {
			t.Fatal("recoverable work lost")
		}
		if e = service.Run(ctx, job); e != nil {
			t.Fatal("pool recovery", e)
		}
		counts(t, 4, 2)
	})
	t.Run("constant statement count and actual query plan", func(t *testing.T) {
		makeRows := func(n, expired int) ([]string, []string) {
			logs, indexes := make([]string, n), make([]string, n)
			for i := range logs {
				changes := map[string]any{"id": 1000000 + i}
				if i >= expired {
					changes["timestamp"] = fixture.Now.Format(time.RFC3339Nano)
				}
				logs[i] = clone(t, baseLogs[0], changes)
				indexes[i] = clone(t, baseIndexes[0], map[string]any{"audit_log_index_id": uuid.NewString(), "log_entry_id": 1000000 + i})
			}
			return logs, indexes
		}
		for _, n := range []int{0, 1, 16, 128, 512} {
			logs, indexes := makeRows(n, n)
			reset(t, logs, indexes)
			job := claim(t)
			trace.count.Store(0)
			if e := service.Run(ctx, job); e != nil {
				t.Fatal(e)
			}
			if trace.count.Load() != 4 {
				t.Fatalf("%d logs: %d statements", n, trace.count.Load())
			}
			counts(t, 0, 0)
		}
		logs, indexes := makeRows(4096, 128)
		reset(t, logs, indexes)
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
				if n["Operation"] == "Delete" && n["Actual Rows"] == float64(128) {
					if name, ok := n["Relation Name"].(string); ok {
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
		if !relations["auditlog_logentry"] || !relations["application_auditlogindex"] {
			t.Fatal("plan did not delete 128 logs and dependent indexes")
		}
		if e = os.WriteFile(os.Getenv("ASSOZETA_AUDIT_PLAN_OUTPUT"), []byte(plan), 0600); e != nil {
			t.Fatal(e)
		}
		counts(t, 4096, 4096)
	})
	t.Run("heartbeat during retention", func(t *testing.T) {
		reset(t, baseLogs, baseIndexes)
		job := enqueue(t)
		exec(t, `CREATE FUNCTION contract_retention_pause() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(0.3); RETURN NULL; END $$`)
		defer exec(t, `DROP FUNCTION contract_retention_pause()`)
		exec(t, `CREATE TRIGGER contract_retention_pause BEFORE DELETE ON auditlog_logentry FOR EACH STATEMENT EXECUTE FUNCTION contract_retention_pause()`)
		defer exec(t, `DROP TRIGGER contract_retention_pause ON auditlog_logentry`)
		gate := NewJobRestoreAdmission(repository.NewRestoreFileLock(""), repository.NewRestoreStates(db), false)
		worker, e := NewJobExecutor(queue, gate, map[string]JobDefinition{domain.JobDeleteOldAuditLogs: {Version: 1, Timeout: 2 * time.Second, Run: service.Run}}, JobExecutorOptions{Owner: "fixture", Concurrency: 1, Lease: time.Second, RenewInterval: 50 * time.Millisecond, StoreTimeout: 200 * time.Millisecond, PollInterval: time.Millisecond, MaintenanceInterval: time.Second, DrainTimeout: time.Second})
		if e != nil {
			t.Fatal(e)
		}
		if ran, e := worker.ExecuteNext(ctx); e != nil || !ran {
			t.Fatal("executor", e)
		}
		if state(t, job).State != "completed" {
			t.Fatal("heartbeat invalidated completion")
		}
		counts(t, 4, 2)
	})
	reset(t, baseLogs, baseIndexes)
	job := claim(t)
	if err = service.Run(ctx, job); err != nil {
		t.Fatal(err)
	}
	exec(t, `DELETE FROM assozeta_go_jobs`)
}
