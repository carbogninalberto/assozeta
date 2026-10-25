//go:build occurrenceintegration

package repository_test

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/testutil/transactionbarrier"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jmoiron/sqlx"
)

// The private runner first applies the actual pinned Django Beat migrations.
// Missing fixtures are errors, never skipped or replaced with invented tables.
func TestPeriodicOccurrencesPostgres(t *testing.T) {
	dsn := os.Getenv("ASSOZETA_OCCURRENCE_TEST_DSN")
	if dsn == "" {
		t.Fatal("private actual-Django PostgreSQL fixture required")
	}
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal("invalid private fixture DSN")
	}
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	db := sqlx.NewDb(ownedpgx.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(12)
	db.SetMaxIdleConns(12)
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	exec := func(query string, args ...any) {
		t.Helper()
		if _, e := db.ExecContext(ctx, query, args...); e != nil {
			t.Fatal(e)
		}
	}
	var database string
	if err = db.GetContext(ctx, &database, `SELECT current_database()`); err != nil || database != "assozeta_contract_occurrence" {
		t.Fatal("wrong private fixture database")
	}
	var history string
	if err = db.GetContext(ctx, &history, `SELECT jsonb_agg(to_jsonb(m) ORDER BY id)::text FROM django_migrations m`); err != nil || !strings.Contains(history, "django_celery_beat") {
		t.Fatal("real Beat history missing", err)
	}
	// Stage each additive migration explicitly so populated pre-v3 operational
	// intent and actual Django source rows are checked across the new boundary.
	var oldIntent, oldSources string
	for _, name := range []string{"000001_durable_jobs.up.sql", "000002_job_ownership.up.sql", "000003_periodic_occurrences.up.sql"} {
		if name == "000003_periodic_occurrences.up.sql" {
			exec(`INSERT INTO assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at) VALUES('00000000-0000-4000-8000-000000000901','00000000-0000-0000-0000-000000000000','contract.retained',1,'prior-intent','{"retained":true}',3,'2020-01-01Z')`)
			if e := db.GetContext(ctx, &oldIntent, `SELECT to_jsonb(j)::text FROM assozeta_go_jobs j`); e != nil {
				t.Fatal(e)
			}
			if e := db.GetContext(ctx, &oldSources, `SELECT jsonb_agg(to_jsonb(p) ORDER BY id)::text FROM django_celery_beat_periodictask p`); e != nil || oldSources == "null" {
				t.Fatal("populated actual Django fixture required", e)
			}
		}
		raw, e := os.ReadFile(filepath.Join("..", "..", "migrations", name))
		if e != nil {
			t.Fatal(e)
		}
		exec(string(raw))
	}
	var retainedIntent, retainedSources, owner string
	var generation int
	if e := db.GetContext(ctx, &retainedIntent, `SELECT to_jsonb(j)::text FROM assozeta_go_jobs j`); e != nil || retainedIntent != oldIntent {
		t.Fatal("migration changed existing intent", e)
	}
	if e := db.GetContext(ctx, &retainedSources, `SELECT jsonb_agg(to_jsonb(p) ORDER BY id)::text FROM django_celery_beat_periodictask p`); e != nil || retainedSources != oldSources {
		t.Fatal("migration changed existing schedules", e)
	}
	if e := db.QueryRowContext(ctx, `SELECT runtime,generation FROM assozeta_go_job_ownership`).Scan(&owner, &generation); e != nil || owner != "django" || generation != 1 {
		t.Fatal("migration changed default ownership", e)
	}
	exec(`CREATE TABLE instance_datarestore(state text,completed_at timestamptz)`)
	store := repository.NewOccurrences(db)
	lock := repository.NewRestoreFileLock(filepath.Join(t.TempDir(), "operations.lock"))
	publisher := service.NewOccurrencePublisher(store, lock, repository.NewRestoreStates(db))
	var sourceID, definitionID int64
	seed := func() {
		t.Helper()
		exec(`TRUNCATE assozeta_go_periodic_occurrences,assozeta_go_jobs; DELETE FROM django_celery_beat_periodictask; DELETE FROM instance_datarestore`)
		exec(`UPDATE assozeta_go_job_ownership SET runtime='go',generation=2 WHERE singleton=1`)
		exec(`INSERT INTO django_celery_beat_periodictasks(ident,last_update) VALUES(1,'2000-01-01Z') ON CONFLICT(ident) DO UPDATE SET last_update=EXCLUDED.last_update`)
		if e := db.GetContext(ctx, &definitionID, `INSERT INTO django_celery_beat_intervalschedule(every,period) VALUES(60,'seconds') RETURNING id`); e != nil {
			t.Fatal(e)
		}
		if e := db.GetContext(ctx, &sourceID, `INSERT INTO django_celery_beat_periodictask
 (name,task,args,kwargs,headers,enabled,last_run_at,total_run_count,date_changed,description,one_off,interval_id)
 VALUES('fixture-reset','reset_communication_daily_email_balance','[]','{}','{}',true,clock_timestamp()-interval '10 minutes',7,clock_timestamp()-interval '1 hour','retained description',false,$1) RETURNING id`, definitionID); e != nil {
			t.Fatal(e)
		}
	}
	counts := func() (jobs, receipts int) {
		t.Helper()
		if e := db.QueryRowContext(ctx, `SELECT (SELECT count(*) FROM assozeta_go_jobs),(SELECT count(*) FROM assozeta_go_periodic_occurrences)`).Scan(&jobs, &receipts); e != nil {
			t.Fatal(e)
		}
		return
	}
	sourceState := func() string {
		t.Helper()
		var value string
		if e := db.GetContext(ctx, &value, `SELECT to_jsonb(p)::text FROM django_celery_beat_periodictask p WHERE id=$1`, sourceID); e != nil {
			t.Fatal(e)
		}
		return value
	}
	unchanged := func(before string) {
		t.Helper()
		j, r := counts()
		if j != 0 || r != 0 || sourceState() != before {
			t.Fatal("partial publication escaped rollback", j, r)
		}
	}
	publish := func() domain.OccurrenceResult {
		t.Helper()
		result, e := publisher.PublishNext(ctx, sourceID)
		if e != nil || !result.Published {
			t.Fatal("publication failed", e)
		}
		return result
	}

	t.Run("default Django ownership leaves source receipt and jobs unchanged", func(t *testing.T) {
		seed()
		exec(`UPDATE assozeta_go_job_ownership SET runtime='django',generation=1`)
		before := sourceState()
		if _, e := publisher.PublishNext(ctx, sourceID); !errors.Is(e, domain.ErrOccurrenceInactive) {
			t.Fatal(e)
		}
		unchanged(before)
	})
	t.Run("publication commits exact intent cursor and retained receipt without marker mutation", func(t *testing.T) {
		seed()
		before := time.Now()
		result := publish()
		after := time.Now()
		job, e := repository.NewJobs(db).Get(ctx, uuid.Nil, result.Receipt.JobID)
		if e != nil {
			t.Fatal(e)
		}
		if job.Kind != domain.JobResetDailyEmailBalance || job.Version != 1 || string(job.Payload) != "{}" || job.MaxAttempts != 3 || job.Attempts != 0 || job.State != "pending" {
			t.Fatal("incorrect durable intent", job)
		}
		var last, changed, marker time.Time
		var count int
		var description string
		if e = db.QueryRowContext(ctx, `SELECT last_run_at,date_changed,total_run_count,description,(SELECT last_update FROM django_celery_beat_periodictasks WHERE ident=1) FROM django_celery_beat_periodictask WHERE id=$1`, sourceID).Scan(&last, &changed, &count, &description, &marker); e != nil {
			t.Fatal(e)
		}
		if !last.Equal(result.Receipt.DispatchedAt) || last.Before(before) || last.After(after) || changed.Before(last) || count != 8 || description != "retained description" || marker.Year() != 2000 {
			t.Fatal("Django cursor or marker changed incorrectly")
		}
		if result.Receipt.Sequence != 1 || len(result.Receipt.DefinitionDigest) != 64 {
			t.Fatal("receipt incomplete")
		}
		second, e := publisher.PublishNext(ctx, sourceID)
		if e != nil || second.Published {
			t.Fatal("early duplicate", e)
		}
		if j, r := counts(); j != 1 || r != 1 {
			t.Fatal(j, r)
		}
	})
	t.Run("concurrent publishers commit one occurrence", func(t *testing.T) {
		seed()
		var wait sync.WaitGroup
		var guard sync.Mutex
		published := 0
		for i := 0; i < 8; i++ {
			wait.Add(1)
			go func() {
				defer wait.Done()
				result, e := publisher.PublishNext(ctx, sourceID)
				guard.Lock()
				defer guard.Unlock()
				if e != nil && !errors.Is(e, domain.ErrOccurrenceBusy) {
					t.Error(e)
				}
				if result.Published {
					published++
				}
			}()
		}
		wait.Wait()
		if j, r := counts(); j != 1 || r != 1 || published != 1 {
			t.Fatal(j, r, published)
		}
	})
	t.Run("unsupported options are retained and never published", func(t *testing.T) {
		for _, change := range []string{`queue='custom'`, `exchange='custom'`, `routing_key='custom'`, `priority=0`, `one_off=true`, `expire_seconds=3`, `expires=clock_timestamp()+interval '1 day'`, `args='[1]'`, `kwargs='{"flag":true}'`, `headers='{"flag":true}'`, `enabled=false`} {
			seed()
			exec(`UPDATE django_celery_beat_periodictask SET `+change+` WHERE id=$1`, sourceID)
			before := sourceState()
			result, e := publisher.PublishNext(ctx, sourceID)
			if result.Published || (change != `enabled=false` && !errors.Is(e, domain.ErrScheduleUnsupported)) {
				t.Fatal(change, e)
			}
			unchanged(before)
		}
	})
	t.Run("UTC daily definition is mapped from the locked actual Django table", func(t *testing.T) {
		seed()
		var cron int64
		if e := db.GetContext(ctx, &cron, `INSERT INTO django_celery_beat_crontabschedule(minute,hour,day_of_week,day_of_month,month_of_year,timezone) VALUES('0','0','*','*','*','UTC') RETURNING id`); e != nil {
			t.Fatal(e)
		}
		exec(`UPDATE django_celery_beat_periodictask SET crontab_id=$2,last_run_at=clock_timestamp()-interval '2 days' WHERE id=$1`, sourceID, cron)
		if _, e := publisher.PublishNext(ctx, sourceID); !errors.Is(e, domain.ErrScheduleUnsupported) {
			t.Fatal("mixed schedule FKs accepted", e)
		}
		exec(`UPDATE django_celery_beat_periodictask SET interval_id=NULL WHERE id=$1`, sourceID)
		publish()
	})
	t.Run("source editor and uncommitted delete produce contention without partial writes", func(t *testing.T) {
		for _, statement := range []string{`UPDATE django_celery_beat_periodictask SET enabled=false WHERE id=$1`, `DELETE FROM django_celery_beat_periodictask WHERE id=$1`} {
			seed()
			before := sourceState()
			tx, e := db.BeginTxx(ctx, nil)
			if e != nil {
				t.Fatal(e)
			}
			if _, e = tx.ExecContext(ctx, statement, sourceID); e != nil {
				_ = tx.Rollback()
				t.Fatal(e)
			}
			_, e = publisher.PublishNext(ctx, sourceID)
			_ = tx.Rollback()
			if !errors.Is(e, domain.ErrOccurrenceBusy) {
				t.Fatal(e)
			}
			unchanged(before)
		}
	})
	t.Run("definition-first editor can acquire parent after publisher backs off", func(t *testing.T) {
		seed()
		before := sourceState()
		tx, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer tx.Rollback()
		if _, e = tx.ExecContext(ctx, `UPDATE django_celery_beat_intervalschedule SET every=120 WHERE id=$1`, definitionID); e != nil {
			t.Fatal(e)
		}
		if _, e = publisher.PublishNext(ctx, sourceID); !errors.Is(e, domain.ErrOccurrenceBusy) {
			t.Fatal(e)
		}
		if _, e = tx.ExecContext(ctx, `SELECT id FROM django_celery_beat_periodictask WHERE id=$1 FOR UPDATE NOWAIT`, sourceID); e != nil {
			t.Fatal("publisher retained source lock", e)
		}
		_ = tx.Rollback()
		unchanged(before)
	})
	t.Run("publication retains owner source and definition locks through transaction outcome", func(t *testing.T) {
		seed()
		tx, e := store.BeginOccurrence(ctx, sourceID)
		if e != nil {
			t.Fatal(e)
		}
		defer tx.Rollback()
		for _, query := range []string{`SELECT singleton FROM assozeta_go_job_ownership FOR UPDATE NOWAIT`, `SELECT id FROM django_celery_beat_periodictask FOR UPDATE NOWAIT`, `SELECT id FROM django_celery_beat_intervalschedule WHERE id=` + fmtInt(definitionID) + ` FOR UPDATE NOWAIT`} {
			_, e = db.ExecContext(ctx, query)
			if e == nil {
				t.Fatal("missing retained lock", query)
			}
		}
		_ = tx.Rollback()
	})
	t.Run("owner lock is acquired before source and rechecks transition", func(t *testing.T) {
		seed()
		owner, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer owner.Rollback()
		if _, e = owner.ExecContext(ctx, `UPDATE assozeta_go_job_ownership SET runtime='django',generation=generation+1`); e != nil {
			t.Fatal(e)
		}
		done := make(chan error, 1)
		go func() { _, e := publisher.PublishNext(ctx, sourceID); done <- e }()
		deadline := time.Now().Add(3 * time.Second)
		for {
			var waiting bool
			e = db.GetContext(ctx, &waiting, `SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE 'SELECT generation FROM public.assozeta_go_job_ownership%')`)
			if e != nil {
				t.Fatal(e)
			}
			if waiting {
				break
			}
			if time.Now().After(deadline) {
				t.Fatal("publisher did not wait on owner")
			}
			time.Sleep(time.Millisecond)
		}
		if _, e = owner.ExecContext(ctx, `SELECT id FROM django_celery_beat_periodictask WHERE id=$1 FOR UPDATE NOWAIT`, sourceID); e != nil {
			t.Fatal("source locked before owner", e)
		}
		if e = owner.Commit(); e != nil {
			t.Fatal(e)
		}
		if e = <-done; !errors.Is(e, domain.ErrOccurrenceInactive) {
			t.Fatal(e)
		}
	})
	t.Run("every SQL fault rolls back job receipt and source including commit failure", func(t *testing.T) {
		for _, phase := range []string{"enqueue", "receipt", "cursor", "commit"} {
			seed()
			before := sourceState()
			exec(`CREATE OR REPLACE FUNCTION occurrence_fixture_fault() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'private occurrence fault'; END $$`)
			table, event := "assozeta_go_jobs", "INSERT"
			if phase == "receipt" || phase == "commit" {
				table = "assozeta_go_periodic_occurrences"
			}
			if phase == "cursor" {
				table, event = "django_celery_beat_periodictask", "UPDATE"
			}
			if phase == "commit" {
				exec(`CREATE CONSTRAINT TRIGGER occurrence_fault AFTER INSERT ON ` + table + ` DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION occurrence_fixture_fault()`)
			} else {
				exec(`CREATE TRIGGER occurrence_fault BEFORE ` + event + ` ON ` + table + ` FOR EACH ROW EXECUTE FUNCTION occurrence_fixture_fault()`)
			}
			result, e := publisher.PublishNext(ctx, sourceID)
			exec(`DROP TRIGGER occurrence_fault ON ` + table)
			if e == nil || result.Published {
				t.Fatal("fault falsely committed", phase)
			}
			unchanged(before)
		}
	})
	t.Run("receipts retain ordinal across count reset owner roundtrip and source ID reuse", func(t *testing.T) {
		seed()
		first := publish()
		exec(`UPDATE assozeta_go_job_ownership SET runtime='django',generation=3`)
		exec(`UPDATE assozeta_go_job_ownership SET runtime='go',generation=4`)
		exec(`UPDATE django_celery_beat_periodictask SET total_run_count=0,last_run_at=clock_timestamp()-interval '10 minutes' WHERE id=$1`, sourceID)
		second := publish()
		exec(`DELETE FROM django_celery_beat_periodictask WHERE id=$1`, sourceID)
		exec(`INSERT INTO django_celery_beat_periodictask(id,name,task,args,kwargs,headers,enabled,last_run_at,total_run_count,date_changed,description,one_off,interval_id)
 VALUES($1,'reused-source','reset_communication_daily_email_balance','[]','{}','{}',true,clock_timestamp()-interval '10 minutes',0,clock_timestamp(),'replacement',false,$2)`, sourceID, definitionID)
		third := publish()
		if first.Receipt.Sequence != 1 || second.Receipt.Sequence != 2 || third.Receipt.Sequence != 3 || third.Receipt.SourceName != "reused-source" {
			t.Fatal("ordinal reused")
		}
		if j, r := counts(); j != 3 || r != 3 {
			t.Fatal(j, r)
		}
		_, e := db.ExecContext(ctx, `DELETE FROM assozeta_go_jobs WHERE id=$1`, first.Receipt.JobID)
		if e == nil {
			t.Fatal("receipt allowed referenced intent deletion")
		}
	})
	t.Run("maintenance and missing mandatory file lease cannot mutate source", func(t *testing.T) {
		seed()
		before := sourceState()
		held, e := lock.Acquire(ctx, true, true)
		if e != nil {
			t.Fatal(e)
		}
		_, e = publisher.PublishNext(ctx, sourceID)
		_ = held.Close()
		if !errors.Is(e, domain.ErrRestoreBusy) {
			t.Fatal(e)
		}
		unchanged(before)
		absent := service.NewOccurrencePublisher(store, repository.NewRestoreFileLock(""), repository.NewRestoreStates(db))
		if _, e = absent.PublishNext(ctx, sourceID); !errors.Is(e, domain.ErrRestoreLockRequired) {
			t.Fatal(e)
		}
		unchanged(before)
		exec(`INSERT INTO instance_datarestore(state) VALUES('running')`)
		if _, e = publisher.PublishNext(ctx, sourceID); !errors.Is(e, domain.ErrRestorePending) {
			t.Fatal(e)
		}
		unchanged(before)
		// Fresh publication does not consume a completed-restore cutoff. Hide
		// that column to catch accidental reuse of the worker history query.
		exec(`DELETE FROM instance_datarestore; ALTER TABLE instance_datarestore RENAME COLUMN completed_at TO fixture_hidden_completion`)
		result, err := publisher.PublishNext(ctx, sourceID)
		exec(`ALTER TABLE instance_datarestore RENAME COLUMN fixture_hidden_completion TO completed_at`)
		if err != nil || !result.Published {
			t.Fatal("publication read irrelevant completed restore history", err)
		}
	})
	t.Run("bounded operational crashes become terminal without another receipt", func(t *testing.T) {
		seed()
		result := publish()
		queue := repository.NewJobs(db)
		for attempt := 1; attempt <= 3; attempt++ {
			jobs, e := queue.Claim(ctx, []string{domain.JobResetDailyEmailBalance}, "private-crash", 1, time.Minute)
			if e != nil || len(jobs) != 1 || jobs[0].Attempts != attempt {
				t.Fatal("claim", attempt, e)
			}
			exec(`UPDATE assozeta_go_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=$1`, result.Receipt.JobID)
		}
		jobs, e := queue.Claim(ctx, []string{domain.JobResetDailyEmailBalance}, "private-crash", 1, time.Minute)
		if e != nil || len(jobs) != 0 {
			t.Fatal(e)
		}
		job, e := queue.Get(ctx, uuid.Nil, result.Receipt.JobID)
		if e != nil || job.State != "failed" || job.Attempts != 3 {
			t.Fatal("crash bound lost", e)
		}
		if j, r := counts(); j != 1 || r != 1 {
			t.Fatal(j, r)
		}
	})
	t.Run("real shared lifecycle lease spans commit and cancelled rollback", func(t *testing.T) {
		for _, cancelBeforeCommit := range []bool{false, true} {
			seed()
			before := sourceState()
			callCtx, cancelCall := context.WithCancel(ctx)
			barrier := &occurrenceBarrierStore{store: store, entered: make(chan struct{}), release: make(chan struct{})}
			controlled := service.NewOccurrencePublisher(barrier, lock, repository.NewRestoreStates(db))
			done := make(chan error, 1)
			go func() { _, e := controlled.PublishNext(callCtx, sourceID); done <- e }()
			select {
			case <-barrier.entered:
			case <-ctx.Done():
				cancelCall()
				t.Fatal("publication did not reach commit barrier")
			}
			held, e := lock.Acquire(ctx, true, true)
			if e == nil {
				_ = held.Close()
				cancelCall()
				close(barrier.release)
				<-done
				t.Fatal("exclusive restore entered before publication outcome")
			}
			if !errors.Is(e, domain.ErrRestoreBusy) {
				cancelCall()
				close(barrier.release)
				<-done
				t.Fatal(e)
			}
			if cancelBeforeCommit {
				cancelCall()
			}
			close(barrier.release)
			e = <-done
			cancelCall()
			if cancelBeforeCommit {
				if e == nil {
					t.Fatal("cancelled publication committed")
				}
				unchanged(before)
			} else if e != nil {
				t.Fatal(e)
			}
			held, e = lock.Acquire(ctx, true, true)
			if e != nil {
				t.Fatal("publication leaked lifecycle lease", e)
			}
			_ = held.Close()
		}
	})
	t.Run("cancelled publication joins actual driver rollback and socket cleanup before releasing lifecycle lease", func(t *testing.T) {
		seed()
		before := sourceState()
		private, control, e := transactionbarrier.OpenConfig(cfg)
		if e != nil {
			t.Fatal(e)
		}
		defer private.Close()
		defer control.ReleaseAll()
		rollback, cleanup := control.ArmRollback(), control.ArmClose()
		work, stop := context.WithCancel(ctx)
		defer stop()
		cancelling := &occurrenceCancelStore{store: repository.NewOccurrences(private), cancel: stop, rollbackEntered: rollback.Entered}
		admitted := service.NewOccurrencePublisher(cancelling, lock, repository.NewRestoreStates(private))
		done := make(chan error, 1)
		go func() { _, err := admitted.PublishNext(work, sourceID); done <- err }()
		wait := func(g *transactionbarrier.Gate) {
			t.Helper()
			limit, cancel := context.WithTimeout(ctx, 3*time.Second)
			defer cancel()
			if e := g.Wait(limit); e != nil {
				t.Fatal("driver boundary not reached", e)
			}
		}
		held := func(requireRowLock bool) {
			t.Helper()
			select {
			case e := <-done:
				t.Fatal("publication returned before driver cleanup", e)
			case <-time.After(20 * time.Millisecond):
			}
			lease, e := lock.Acquire(ctx, true, true)
			if lease != nil {
				_ = lease.Close()
			}
			if !errors.Is(e, domain.ErrRestoreBusy) {
				t.Fatal("lifecycle lease released before driver cleanup", e)
			}
			if requireRowLock {
				_, e = db.ExecContext(ctx, `SELECT id FROM django_celery_beat_periodictask WHERE id=$1 FOR UPDATE NOWAIT`, sourceID)
				var pg *pgconn.PgError
				if !errors.As(e, &pg) || pg.Code != "55P03" {
					t.Fatal("actual source row was not still locked", e)
				}
			}
		}
		wait(rollback)
		held(true)
		rollback.Release()
		wait(cleanup)
		held(false)
		cleanup.Release()
		select {
		case e := <-done:
			if !errors.Is(e, context.Canceled) {
				t.Fatal("cancelled publication outcome", e)
			}
		case <-time.After(3 * time.Second):
			t.Fatal("publication cleanup did not finish")
		}
		lease, e := lock.Acquire(ctx, true, true)
		if e != nil {
			t.Fatal("finished publication retained lifecycle lease", e)
		}
		_ = lease.Close()
		unchanged(before)
	})

	t.Run("handled business UPDATE failure completes without business retry", func(t *testing.T) {
		seed()
		result := publish()
		queue := repository.NewJobs(db)
		exec(`CREATE TABLE communications_communicationconfiguration(id integer PRIMARY KEY,daily_email_balance integer); INSERT INTO communications_communicationconfiguration VALUES(1,9)`)
		exec(`CREATE TRIGGER occurrence_business_fault BEFORE UPDATE ON communications_communicationconfiguration FOR EACH ROW EXECUTE FUNCTION occurrence_fixture_fault()`)
		jobs, e := queue.Claim(ctx, []string{domain.JobResetDailyEmailBalance}, "private-handler", 1, time.Minute)
		if e != nil || len(jobs) != 1 {
			t.Fatal(e)
		}
		handler := service.NewEmailBalanceReset(repository.NewEmailBalanceResets(db), slog.New(slog.NewTextHandler(io.Discard, nil)))
		if e = handler.Run(ctx, jobs[0]); e != nil {
			t.Fatal(e)
		}
		job, e := queue.Get(ctx, uuid.Nil, result.Receipt.JobID)
		if e != nil || job.State != "completed" || job.Attempts != 1 {
			t.Fatal("handled business fault retried", e)
		}
		var value int
		if e = db.GetContext(ctx, &value, `SELECT daily_email_balance FROM communications_communicationconfiguration WHERE id=1`); e != nil || value != 9 {
			t.Fatal("failed UPDATE escaped rollback", e)
		}
		jobs, e = queue.Claim(ctx, []string{domain.JobResetDailyEmailBalance}, "private-handler", 1, time.Minute)
		if e != nil || len(jobs) != 0 {
			t.Fatal("handled error redelivered", e)
		}
	})
	t.Run("committed source deletion is absent and migration history remains unchanged", func(t *testing.T) {
		seed()
		exec(`DELETE FROM django_celery_beat_periodictask WHERE id=$1`, sourceID)
		if _, e := publisher.PublishNext(ctx, sourceID); !errors.Is(e, domain.ErrOccurrenceNotFound) {
			t.Fatal(e)
		}
		var after string
		if e := db.GetContext(ctx, &after, `SELECT jsonb_agg(to_jsonb(m) ORDER BY id)::text FROM django_migrations m`); e != nil || after != history {
			t.Fatal("Django migration history modified", e)
		}
		raw, e := os.ReadFile(filepath.Join("..", "..", "migrations", "000003_periodic_occurrences.down.sql"))
		if e != nil {
			t.Fatal(e)
		}
		if _, e = db.ExecContext(ctx, string(raw)); e == nil {
			t.Fatal("destructive down migration allowed")
		}
	})
}

func fmtInt(value int64) string { return strconv.FormatInt(value, 10) }

type occurrenceBarrierStore struct {
	store            domain.OccurrenceStore
	entered, release chan struct{}
}

func (s *occurrenceBarrierStore) BeginOccurrence(ctx context.Context, id int64) (domain.OccurrenceTransaction, error) {
	tx, err := s.store.BeginOccurrence(ctx, id)
	if err != nil {
		return nil, err
	}
	return &occurrenceBarrierTransaction{OccurrenceTransaction: tx, barrier: s}, nil
}

type occurrenceBarrierTransaction struct {
	domain.OccurrenceTransaction
	barrier *occurrenceBarrierStore
}

func (t *occurrenceBarrierTransaction) Publish(ctx context.Context) (domain.OccurrenceReceipt, error) {
	r, err := t.OccurrenceTransaction.Publish(ctx)
	if err != nil {
		return r, err
	}
	close(t.barrier.entered)
	select {
	case <-ctx.Done():
		return domain.OccurrenceReceipt{}, ctx.Err()
	case <-t.barrier.release:
	}
	if err = ctx.Err(); err != nil {
		return domain.OccurrenceReceipt{}, err
	}
	return r, nil
}

// The wrapper only chooses cancellation after actual source/job/receipt SQL.
// Waiting for the real driver gate makes database/sql automatic rollback win
// deterministically before control returns to the service's deferred rollback.
type occurrenceCancelStore struct {
	store           domain.OccurrenceStore
	cancel          context.CancelFunc
	rollbackEntered <-chan struct{}
}

func (s *occurrenceCancelStore) BeginOccurrence(ctx context.Context, id int64) (domain.OccurrenceTransaction, error) {
	tx, e := s.store.BeginOccurrence(ctx, id)
	if e != nil {
		return nil, e
	}
	return &occurrenceCancelTransaction{OccurrenceTransaction: tx, barrier: s}, nil
}

type occurrenceCancelTransaction struct {
	domain.OccurrenceTransaction
	barrier *occurrenceCancelStore
}

func (t *occurrenceCancelTransaction) Publish(ctx context.Context) (domain.OccurrenceReceipt, error) {
	result, e := t.OccurrenceTransaction.Publish(ctx)
	if e != nil {
		return result, e
	}
	t.barrier.cancel()
	select {
	case <-t.barrier.rollbackEntered:
		return domain.OccurrenceReceipt{}, ctx.Err()
	case <-time.After(3 * time.Second):
		return domain.OccurrenceReceipt{}, errors.New("fixture automatic rollback did not enter driver")
	}
}
