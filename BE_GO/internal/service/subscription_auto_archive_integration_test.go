//go:build subscriptionarchiveintegration

package service

import (
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

type archiveQueries struct {
	count, renew atomic.Int64
	mu           sync.Mutex
	page         string
}

func (q *archiveQueries) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	q.count.Add(1)
	if strings.HasPrefix(d.SQL, "SELECT s.subscription_id AS id") {
		q.mu.Lock()
		q.page = d.SQL
		q.mu.Unlock()
	}
	if strings.Contains(d.SQL, "SET lease_until=GREATEST") {
		q.renew.Add(1)
	}
	return ctx
}
func (q *archiveQueries) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

func TestSubscriptionAutoArchivePostgres(t *testing.T) {
	var fixture struct {
		Now  time.Time
		Rows []struct {
			Name, ID, Association, Payment string
			Changed                        bool
			Start, End                     *string
		}
		Audits                  int
		Owner, OtherAssociation string
	}
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_ARCHIVE_FIXTURE"))
	if err != nil || json.Unmarshal(raw, &fixture) != nil || len(fixture.Rows) != 10 {
		t.Fatal("run accounts.py --only subscription-auto-archive")
	}
	dsn := os.Getenv("ASSOZETA_ARCHIVE_DSN")
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal(err)
	}
	trace := &archiveQueries{}
	cfg.Tracer = trace
	db := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	if err = migrations.Apply(dsn); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	exec := func(query string, args ...any) {
		t.Helper()
		if _, e := db.ExecContext(ctx, query, args...); e != nil {
			t.Fatal(e)
		}
	}
	tables := []string{"application_subscription", "application_payment", "application_invoice"}
	baseline := map[string][]string{}
	for _, table := range tables {
		var rows []string
		if e := db.SelectContext(ctx, &rows, "SELECT to_jsonb(r)::text FROM "+table+" r"); e != nil {
			t.Fatal(e)
		}
		baseline[table] = rows
	}
	auditWhere := `content_type_id IN (SELECT id FROM django_content_type WHERE app_label='application' AND model IN ('subscription','payment','invoice'))`
	reset := func() {
		exec(`DELETE FROM application_auditlogindex WHERE log_entry_id IN (SELECT id FROM auditlog_logentry WHERE ` + auditWhere + `)`)
		exec(`DELETE FROM auditlog_logentry WHERE ` + auditWhere)
		for _, table := range tables {
			exec("DELETE FROM " + table)
		}
		for i := len(tables) - 1; i >= 0; i-- {
			table := tables[i]
			for _, row := range baseline[table] {
				exec("INSERT INTO "+table+" SELECT * FROM jsonb_populate_record(NULL::"+table+",$1::jsonb)", row)
			}
		}
		exec(`DELETE FROM assozeta_go_jobs`)
	}
	// Trusted private fixture handoff; no production ownership switch is exposed.
	if _, err = db.Exec(`UPDATE assozeta_go_job_ownership SET runtime='go',generation=generation+1 WHERE singleton=1`); err != nil {
		t.Fatal(err)
	}
	queue := repository.NewJobs(db)
	worker := NewSubscriptionAutoArchive(repository.NewSubscriptionAutoArchives(db), func() time.Time { return fixture.Now }, slog.New(slog.NewTextHandler(io.Discard, nil)))
	claim := func() domain.Job {
		tx, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer tx.Rollback()
		_, e = queue.Enqueue(ctx, tx, domain.JobIntent{ID: uuid.New(), Kind: domain.JobAutoArchiveSubscription, Version: 1, Key: uuid.NewString(), Payload: json.RawMessage(`{}`), MaxAttempts: 2})
		if e != nil {
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
	count := func(query string) int {
		var n int
		if e := db.GetContext(ctx, &n, query); e != nil {
			t.Fatal(e)
		}
		return n
	}
	completed := func(j domain.Job) {
		stored, e := queue.Get(ctx, uuid.Nil, j.ID)
		if e != nil || stored.State != "completed" || stored.Result != nil {
			t.Fatal("None completion", e)
		}
	}
	unchanged := func() {
		for _, table := range tables {
			var rows []string
			if e := db.SelectContext(ctx, &rows, "SELECT to_jsonb(r)::text FROM "+table+" r"); e != nil {
				t.Fatal(e)
			}
			set := map[string]bool{}
			for _, row := range baseline[table] {
				set[row] = true
			}
			if len(rows) != len(set) {
				t.Fatal("row count changed")
			}
			for _, row := range rows {
				if !set[row] {
					t.Fatal("partial business write", table)
				}
			}
		}
		if count("SELECT count(*) FROM auditlog_logentry WHERE "+auditWhere) != 0 {
			t.Fatal("partial audits")
		}
	}
	t.Run("exact archive defaults audits and replay", func(t *testing.T) {
		reset()
		job := claim()
		if e := worker.Run(ctx, job); e != nil {
			t.Fatal(e)
		}
		completed(job)
		if count("SELECT count(*) FROM auditlog_logentry WHERE "+auditWhere) != fixture.Audits {
			t.Fatal("wrong audit count")
		}
		for _, row := range fixture.Rows {
			if row.Changed {
				var archived bool
				if e := db.GetContext(ctx, &archived, `SELECT archived FROM application_subscription WHERE subscription_id=$1`, row.ID); e != nil || !archived {
					t.Fatal(row.Name, e)
				}
			}
		}
		again := claim()
		if e := worker.Run(ctx, again); e != nil {
			t.Fatal(e)
		}
		completed(again)
		if count("SELECT count(*) FROM auditlog_logentry WHERE "+auditWhere) != fixture.Audits {
			t.Fatal("replay duplicated audits")
		}
	})
	t.Run("invalid envelope before SQL", func(t *testing.T) {
		reset()
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
		unchanged()
	})
	t.Run("optional relationships remain supported", func(t *testing.T) {
		reset()
		exec(`UPDATE application_subscription SET payment_id=NULL WHERE subscription_id=$1`, fixture.Rows[0].ID)
		exec(`UPDATE application_payment SET invoice_id=NULL WHERE payment_id=$1`, fixture.Rows[1].Payment)
		job := claim()
		if e := worker.Run(ctx, job); e != nil {
			t.Fatal(e)
		}
		completed(job)
		for _, row := range fixture.Rows {
			if row.Changed {
				var archived bool
				if e := db.GetContext(ctx, &archived, `SELECT archived FROM application_subscription WHERE subscription_id=$1`, row.ID); e != nil || !archived {
					t.Fatal("optional link stopped archive", row.Name, e)
				}
			}
		}
	})
	t.Run("foreign payment fails atomically", func(t *testing.T) {
		reset()
		exec(`UPDATE application_payment SET sport_association_id=$2 WHERE payment_id=$1`, fixture.Rows[0].Payment, fixture.OtherAssociation)
		job := claim()
		if e := worker.Run(ctx, job); e != nil {
			t.Fatal("handled business error", e)
		}
		completed(job)
		exec(`UPDATE application_payment SET sport_association_id=$2 WHERE payment_id=$1`, fixture.Rows[0].Payment, fixture.Rows[0].Association)
		unchanged()
	})
	t.Run("audit and index failures roll back the entire archive", func(t *testing.T) {
		for _, table := range []string{"application_subscription", "application_payment", "application_invoice", "auditlog_logentry", "application_auditlogindex"} {
			reset()
			job := claim()
			op := "UPDATE"
			if table == "auditlog_logentry" || table == "application_auditlogindex" {
				op = "INSERT"
			}
			exec(`CREATE FUNCTION contract_archive_fail() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'private archive failure'; END $$`)
			exec("CREATE TRIGGER contract_archive_fail BEFORE " + op + " ON " + table + " FOR EACH STATEMENT EXECUTE FUNCTION contract_archive_fail()")
			e := worker.Run(ctx, job)
			exec("DROP TRIGGER contract_archive_fail ON " + table)
			exec(`DROP FUNCTION contract_archive_fail()`)
			if e != nil {
				t.Fatal("handled business failure", table, e)
			}
			completed(job)
			unchanged()
		}
	})
	t.Run("stale completion rolls back all effects", func(t *testing.T) {
		reset()
		job := claim()
		exec(`UPDATE assozeta_go_jobs SET lease_token=$2 WHERE id=$1`, job.ID, uuid.New())
		if e := worker.Run(ctx, job); !errors.Is(e, domain.ErrJobLeaseLost) {
			t.Fatal("stale completion", e)
		}
		unchanged()
	})
	t.Run("strict fiscal threshold and invalid calendars", func(t *testing.T) {
		reset()
		at := time.Date(2029, 12, 31, 0, 0, 0, 0, time.UTC)
		timed := NewSubscriptionAutoArchive(repository.NewSubscriptionAutoArchives(db), func() time.Time { return at }, slog.New(slog.NewTextHandler(io.Discard, nil)))
		job := claim()
		if e := timed.Run(ctx, job); e != nil {
			t.Fatal(e)
		}
		completed(job)
		var archived bool
		if e := db.GetContext(ctx, &archived, `SELECT archived FROM application_subscription WHERE subscription_id=$1`, fixture.Rows[1].ID); e != nil || archived {
			t.Fatal("equality crossed fiscal threshold", e)
		}
		at = at.Add(time.Microsecond)
		job = claim()
		if e := timed.Run(ctx, job); e != nil {
			t.Fatal(e)
		}
		completed(job)
		if e := db.GetContext(ctx, &archived, `SELECT archived FROM application_subscription WHERE subscription_id=$1`, fixture.Rows[1].ID); e != nil || !archived {
			t.Fatal("strict boundary did not archive", e)
		}
		for _, column := range []string{"balance_sheet_start_month", "subscription_start_month"} {
			reset()
			var old int
			if e := db.GetContext(ctx, &old, "SELECT "+column+" FROM bakney_user WHERE user_id=$1", fixture.Owner); e != nil {
				t.Fatal(e)
			}
			exec("UPDATE bakney_user SET "+column+"=0 WHERE user_id=$1", fixture.Owner)
			job = claim()
			e := worker.Run(ctx, job)
			exec("UPDATE bakney_user SET "+column+"=$2 WHERE user_id=$1", fixture.Owner, old)
			if e != nil {
				t.Fatal(e)
			}
			completed(job)
			unchanged()
		}
	})
	t.Run("bounded pages statement counts and executed plan", func(t *testing.T) {
		var seed string
		if e := db.GetContext(ctx, &seed, `SELECT to_jsonb(s)::text FROM application_subscription s WHERE subscription_id=$1`, fixture.Rows[0].ID); e != nil {
			t.Fatal(e)
		}
		seedRows := func(n int) {
			reset()
			exec(`DELETE FROM application_subscription`)
			exec(`INSERT INTO application_subscription SELECT (jsonb_populate_record(NULL::application_subscription,
    $1::jsonb||jsonb_build_object('subscription_id',md5('archive-fixture-'||n::text)::uuid,'payment_id',NULL,'archived',FALSE))).*
    FROM generate_series(1,$2) n`, seed, n)
		}
		for _, n := range []int{0, 1, 16, 100, 101, 512} {
			seedRows(n)
			job := claim()
			trace.count.Store(0)
			if e := worker.Run(ctx, job); e != nil {
				t.Fatal(e)
			}
			got := trace.count.Load()
			want := int64(2*((n+99)/100) + 5)
			if got != want {
				t.Fatalf("%d rows: %d statements want %d", n, got, want)
			}
			completed(job)
			if count(`SELECT count(*) FROM application_subscription WHERE archived`) != n || count("SELECT count(*) FROM auditlog_logentry WHERE "+auditWhere) != n {
				t.Fatal("page skipped or duplicated rows")
			}
		}
		for _, n := range []int{1, 16, 128, 512} {
			seedRows(n)
			exec(`INSERT INTO application_invoice SELECT (jsonb_populate_record(NULL::application_invoice,
                to_jsonb(i)||jsonb_build_object('invoice_id',md5('archive-scaled-invoice-'||g.n::text)::uuid))).*
                FROM application_invoice i CROSS JOIN generate_series(1,$2) g(n)
                WHERE i.invoice_id=(SELECT invoice_id FROM application_payment WHERE payment_id=$1)`, fixture.Rows[0].Payment, n)
			exec(`INSERT INTO application_payment SELECT (jsonb_populate_record(NULL::application_payment,
                to_jsonb(p)||jsonb_build_object('payment_id',md5('archive-scaled-payment-'||g.n::text)::uuid,
                'invoice_id',md5('archive-scaled-invoice-'||g.n::text)::uuid))).*
                FROM application_payment p CROSS JOIN generate_series(1,$2) g(n) WHERE p.payment_id=$1`, fixture.Rows[0].Payment, n)
			exec(`UPDATE application_subscription s SET payment_id=md5('archive-scaled-payment-'||g.n::text)::uuid
                FROM generate_series(1,$1) g(n) WHERE s.subscription_id=md5('archive-fixture-'||g.n::text)::uuid`, n)
			job := claim()
			trace.count.Store(0)
			if e := worker.Run(ctx, job); e != nil {
				t.Fatal(e)
			}
			got := trace.count.Load()
			want := int64(6*((n+99)/100) + 5)
			if got != want {
				t.Fatalf("%d linked rows: %d statements want %d", n, got, want)
			}
			completed(job)
			if count("SELECT count(*) FROM auditlog_logentry WHERE "+auditWhere) != 3*n {
				t.Fatal("linked page audit count")
			}
		}
		seedRows(4096)
		exec(`ANALYZE application_subscription,bakney_user,application_sportassociation`)
		trace.mu.Lock()
		query := trace.page
		trace.mu.Unlock()
		tx, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer tx.Rollback()
		var plan string
		e = tx.GetContext(ctx, &plan, "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) "+query, nil, 100)
		_ = tx.Rollback()
		if e != nil {
			t.Fatal(e)
		}
		var decoded []struct{ Plan map[string]any }
		if json.Unmarshal([]byte(plan), &decoded) != nil || len(decoded) != 1 || decoded[0].Plan["Actual Rows"] != float64(100) {
			t.Fatal("page plan did not execute 100 rows")
		}
		if e = os.WriteFile(os.Getenv("ASSOZETA_ARCHIVE_PLAN"), []byte(plan), 0600); e != nil {
			t.Fatal(e)
		}
		if db.Stats().InUse != 0 || db.Stats().MaxOpenConnections != 4 {
			t.Fatal("page plan leaked connections")
		}
	})
	t.Run("blocked query cancellation and connection recovery", func(t *testing.T) {
		reset()
		job := claim()
		blocker, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer blocker.Rollback()
		if _, e = blocker.ExecContext(ctx, `LOCK TABLE application_subscription IN ACCESS EXCLUSIVE MODE`); e != nil {
			t.Fatal(e)
		}
		var pid int
		if e = blocker.GetContext(ctx, &pid, `SELECT pg_backend_pid()`); e != nil {
			t.Fatal(e)
		}
		blockedCtx, stop := context.WithCancel(ctx)
		defer stop()
		done := make(chan error, 1)
		go func() { done <- worker.Run(blockedCtx, job) }()
		observed := false
		until := time.Now().Add(3 * time.Second)
		for !observed && time.Now().Before(until) {
			if e = db.GetContext(ctx, &observed, `SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' AND $1=ANY(pg_blocking_pids(pid)))`, pid); e != nil {
				break
			}
			if !observed {
				time.Sleep(5 * time.Millisecond)
			}
		}
		stop()
		runErr := <-done
		_ = blocker.Rollback()
		if !observed || e != nil || !errors.Is(runErr, context.Canceled) {
			t.Fatal("actual blocked cancellation", observed, e, runErr)
		}
		unchanged()
		stored, e := queue.Get(ctx, uuid.Nil, job.ID)
		if e != nil || stored.State != "running" {
			t.Fatal("canceled work lost", e)
		}
		if e = worker.Run(ctx, job); e != nil {
			t.Fatal("pool recovery", e)
		}
		completed(job)
		if db.Stats().InUse != 0 {
			t.Fatal("connections leaked")
		}
	})
	t.Run("concurrent archives audit each change once", func(t *testing.T) {
		reset()
		first, second := claim(), claim()
		exec(`CREATE FUNCTION contract_archive_pause() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(0.2); RETURN NULL; END $$`)
		exec(`CREATE TRIGGER contract_archive_pause BEFORE UPDATE ON application_subscription FOR EACH STATEMENT EXECUTE FUNCTION contract_archive_pause()`)
		start := make(chan struct{})
		done := make(chan error, 2)
		for _, job := range []domain.Job{first, second} {
			go func(j domain.Job) { <-start; done <- worker.Run(ctx, j) }(job)
		}
		close(start)
		observed := false
		until := time.Now().Add(2 * time.Second)
		for !observed && time.Now().Before(until) {
			if e := db.GetContext(ctx, &observed, `SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE 'SELECT s.subscription_id AS id%')`); e != nil {
				t.Fatal(e)
			}
			if !observed {
				time.Sleep(5 * time.Millisecond)
			}
		}
		a, b := <-done, <-done
		exec(`DROP TRIGGER contract_archive_pause ON application_subscription`)
		exec(`DROP FUNCTION contract_archive_pause()`)
		if !observed || a != nil || b != nil {
			t.Fatal("concurrent archive", observed, a, b)
		}
		completed(first)
		completed(second)
		if count("SELECT count(*) FROM auditlog_logentry WHERE "+auditWhere) != fixture.Audits {
			t.Fatal("duplicate audit")
		}
	})
	t.Run("executor heartbeat during archive", func(t *testing.T) {
		reset()
		job := claim()
		// Requeue only this private fixture job so the real executor performs the claim.
		exec(`UPDATE assozeta_go_jobs SET state='pending',lease_owner='',lease_token='00000000-0000-0000-0000-000000000000',lease_until=NULL WHERE id=$1`, job.ID)
		exec(`CREATE FUNCTION contract_archive_pause() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(0.3); RETURN NULL; END $$`)
		exec(`CREATE TRIGGER contract_archive_pause BEFORE UPDATE ON application_subscription FOR EACH STATEMENT EXECUTE FUNCTION contract_archive_pause()`)
		gate := NewJobRestoreAdmission(repository.NewRestoreFileLock(""), repository.NewRestoreStates(db), false)
		executor, e := NewJobExecutor(queue, gate, map[string]JobDefinition{domain.JobAutoArchiveSubscription: {Version: 1, Timeout: 3 * time.Second, Run: worker.Run}}, JobExecutorOptions{Owner: "fixture", Concurrency: 1, Lease: time.Second, RenewInterval: 50 * time.Millisecond, StoreTimeout: 200 * time.Millisecond, PollInterval: time.Millisecond, MaintenanceInterval: time.Second, DrainTimeout: time.Second})
		if e != nil {
			t.Fatal(e)
		}
		trace.renew.Store(0)
		ran, e := executor.ExecuteNext(ctx)
		exec(`DROP TRIGGER contract_archive_pause ON application_subscription`)
		exec(`DROP FUNCTION contract_archive_pause()`)
		if e != nil || !ran || trace.renew.Load() == 0 {
			t.Fatal("heartbeat", ran, e, trace.renew.Load())
		}
		completed(job)
		if count("SELECT count(*) FROM auditlog_logentry WHERE "+auditWhere) != fixture.Audits {
			t.Fatal("heartbeat lost effects")
		}
	})
	t.Run("later page failure rolls back earlier changes", func(t *testing.T) {
		reset()
		var seed string
		if e := db.GetContext(ctx, &seed, `SELECT to_jsonb(s)::text FROM application_subscription s WHERE subscription_id=$1`, fixture.Rows[0].ID); e != nil {
			t.Fatal(e)
		}
		exec(`INSERT INTO application_subscription SELECT (jsonb_populate_record(NULL::application_subscription,
   $1::jsonb||jsonb_build_object('subscription_id',md5('archive-failure-'||n::text)::uuid,'payment_id',NULL,'archived',FALSE))).*
   FROM generate_series(1,120) n`, seed)
		var before string
		if e := db.GetContext(ctx, &before, `SELECT jsonb_agg(to_jsonb(s) ORDER BY subscription_id)::text FROM application_subscription s`); e != nil {
			t.Fatal(e)
		}
		exec(`CREATE FUNCTION contract_archive_second_page() RETURNS trigger LANGUAGE plpgsql AS $$ DECLARE n integer; BEGIN
   n:=COALESCE(NULLIF(current_setting('assozeta.archive_batches',true),''),'0')::integer+1;
   PERFORM set_config('assozeta.archive_batches',n::text,true);
   IF n=2 THEN RAISE EXCEPTION 'private second page failure'; END IF; RETURN NULL; END $$`)
		exec(`CREATE TRIGGER contract_archive_second_page BEFORE UPDATE ON application_subscription FOR EACH STATEMENT EXECUTE FUNCTION contract_archive_second_page()`)
		job := claim()
		e := worker.Run(ctx, job)
		exec(`DROP TRIGGER contract_archive_second_page ON application_subscription`)
		exec(`DROP FUNCTION contract_archive_second_page()`)
		if e != nil {
			t.Fatal(e)
		}
		completed(job)
		var after string
		if e = db.GetContext(ctx, &after, `SELECT jsonb_agg(to_jsonb(s) ORDER BY subscription_id)::text FROM application_subscription s`); e != nil {
			t.Fatal(e)
		}
		if before != after || count("SELECT count(*) FROM auditlog_logentry WHERE "+auditWhere) != 0 {
			t.Fatal("earlier page escaped rollback")
		}
		if count(`SELECT count(*) FROM application_payment WHERE archived`) != 1 || count(`SELECT count(*) FROM application_invoice WHERE archived`) != 1 {
			t.Fatal("linked archive escaped rollback")
		}
	})
	t.Run("historical linked changes use stored values and direct indexes", func(t *testing.T) {
		reset()
		exec(`UPDATE application_payment SET archived=FALSE WHERE deleted`)
		exec(`UPDATE application_invoice SET archived=FALSE WHERE deleted`)
		job := claim()
		if e := worker.Run(ctx, job); e != nil {
			t.Fatal(e)
		}
		completed(job)
		var n int
		if e := db.GetContext(ctx, &n, `SELECT count(*) FROM auditlog_logentry l JOIN django_content_type c ON c.id=l.content_type_id
   JOIN application_auditlogindex i ON i.log_entry_id=l.id
   WHERE c.app_label='application' AND c.model IN ('payment','invoice') AND i.resolution_path='direct'
   AND l.changes='{"archived":["False","True"]}'::jsonb
   AND (l.object_pk IN (SELECT payment_id::text FROM application_payment WHERE deleted)
     OR l.object_pk IN (SELECT invoice_id::text FROM application_invoice WHERE deleted))`); e != nil || n != 2 {
			t.Fatal("historical row audit/index mismatch", n, e)
		}
		if count("SELECT count(*) FROM auditlog_logentry WHERE "+auditWhere) != fixture.Audits+2 {
			t.Fatal("historical audits were duplicated")
		}
	})
	reset()
	job := claim()
	if err = worker.Run(ctx, job); err != nil {
		t.Fatal(err)
	}
	exec(`DELETE FROM assozeta_go_jobs`)
}
