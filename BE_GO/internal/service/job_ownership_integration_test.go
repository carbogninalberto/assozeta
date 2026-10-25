//go:build jobsintegration

package service

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"os"
	"sync"
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

// All handoffs below are trusted fixture SQL against the private adapter's DSN.
// They are not a production switch, Celery drain or operator activation API.
func TestJobOwnershipPostgres(t *testing.T) {
	dsn := os.Getenv("ASSOZETA_JOB_OWNERSHIP_DSN")
	if dsn == "" {
		t.Fatal("private ownership PostgreSQL fixture required; no skipped integration")
	}
	dbConfig, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal("private database unavailable")
	}
	db := sqlx.NewDb(ownedpgx.OpenDB(*dbConfig), "pgx")
	if err = db.Ping(); err != nil {
		_ = db.Close()
		t.Fatal("private database unavailable")
	}
	defer db.Close()
	db.SetMaxOpenConns(8)
	if err = migrations.Apply(dsn); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	exec := func(query string, args ...any) {
		t.Helper()
		if _, e := db.ExecContext(ctx, query, args...); e != nil {
			t.Fatal(e)
		}
	}
	exec(`CREATE TABLE communications_communicationconfiguration (daily_email_balance integer NOT NULL); INSERT INTO communications_communicationconfiguration VALUES (9)`)
	queue := repository.NewJobs(db)
	reset := NewEmailBalanceReset(repository.NewEmailBalanceResets(db), slog.New(slog.NewTextHandler(io.Discard, nil)))
	publish := func() domain.Job {
		t.Helper()
		tx, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer tx.Rollback()
		j, e := queue.Enqueue(ctx, tx, domain.JobIntent{ID: uuid.New(), Kind: domain.JobResetDailyEmailBalance, Version: 1, Key: uuid.NewString(), Payload: json.RawMessage(`{}`), MaxAttempts: 3})
		if e != nil {
			t.Fatal(e)
		}
		if e = tx.Commit(); e != nil {
			t.Fatal(e)
		}
		return j
	}
	handoff := func(runtime string) {
		t.Helper()
		exec(`UPDATE assozeta_go_job_ownership SET runtime=$1,generation=generation+1 WHERE singleton=1`, runtime)
	}
	claim := func() domain.Job {
		t.Helper()
		jobs, e := queue.Claim(ctx, []string{domain.JobResetDailyEmailBalance}, "ownership-fixture", 1, time.Minute)
		if e != nil || len(jobs) != 1 {
			t.Fatalf("claim: %d %v", len(jobs), e)
		}
		return jobs[0]
	}
	balance := func(want int) {
		t.Helper()
		var got int
		if e := db.GetContext(ctx, &got, `SELECT daily_email_balance FROM communications_communicationconfiguration`); e != nil || got != want {
			t.Fatalf("balance=%d want=%d: %v", got, want, e)
		}
	}
	clean := func() {
		exec(`TRUNCATE assozeta_go_periodic_occurrences,assozeta_go_jobs`)
		exec(`UPDATE communications_communicationconfiguration SET daily_email_balance=9`)
	}
	t.Run("default Django ownership never claims or spends attempts", func(t *testing.T) {
		defer clean()
		j := publish()
		jobs, e := queue.Claim(ctx, []string{j.Kind}, "inactive", 1, time.Minute)
		if e != nil || len(jobs) != 0 {
			t.Fatalf("Django-owned job claimed: %d %v", len(jobs), e)
		}
		got, e := queue.Get(ctx, j.AssociationID, j.ID)
		if e != nil || got.State != "pending" || got.Attempts != 0 {
			t.Fatal("inactive worker mutated durable intent", e)
		}
		var runtime string
		var generation int64
		if e = db.QueryRowContext(ctx, `SELECT runtime,generation FROM assozeta_go_job_ownership WHERE singleton=1`).Scan(&runtime, &generation); e != nil || runtime != "django" || generation != 1 {
			t.Fatal("unsafe initial ownership", e)
		}
	})
	t.Run("lost ownership rejects effects renewal failure and completion", func(t *testing.T) {
		defer clean()
		handoff("go")
		publish()
		j := claim()
		handoff("django")
		if e := reset.Run(ctx, j); !errors.Is(e, domain.ErrJobLeaseLost) {
			t.Fatal("stale service admitted", e)
		}
		for _, e := range []error{queue.Renew(ctx, j.ID, j.LeaseToken, time.Minute), queue.Fail(ctx, j.ID, j.LeaseToken, "retry", true, 0), queue.Complete(ctx, j.ID, j.LeaseToken, nil)} {
			if !errors.Is(e, domain.ErrJobLeaseLost) {
				t.Fatal("stale mutation admitted", e)
			}
		}
		balance(9)
		got, e := queue.Get(ctx, j.AssociationID, j.ID)
		if e != nil || got.State != "running" || got.Attempts != 1 {
			t.Fatal("stale outcome mutated job", e)
		}
	})
	t.Run("returning Go ownership cannot revive prior generation", func(t *testing.T) {
		defer clean()
		handoff("go")
		publish()
		old := claim()
		handoff("django")
		handoff("go")
		if e := reset.Run(ctx, old); !errors.Is(e, domain.ErrJobLeaseLost) {
			t.Fatal("old generation revived", e)
		}
		balance(9)
		exec(`UPDATE assozeta_go_jobs SET lease_until=clock_timestamp()-interval '1 second'`)
		current := claim()
		if current.LeaseToken == old.LeaseToken || current.Attempts != 2 {
			t.Fatal("recovery did not fence prior attempt")
		}
		if e := reset.Run(ctx, current); e != nil {
			t.Fatal(e)
		}
		balance(0)
		if e := queue.Complete(ctx, old.ID, old.LeaseToken, nil); !errors.Is(e, domain.ErrJobLeaseLost) {
			t.Fatal("stale completion accepted", e)
		}
	})
	t.Run("handoff before completion rolls back business transaction", func(t *testing.T) {
		defer clean()
		handoff("go")
		publish()
		j := claim()
		tx, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer tx.Rollback()
		if _, e = tx.ExecContext(ctx, `UPDATE communications_communicationconfiguration SET daily_email_balance=0`); e != nil {
			t.Fatal(e)
		}
		handoff("django")
		if e = queue.CompleteInTx(ctx, tx, j.ID, j.LeaseToken, nil); !errors.Is(e, domain.ErrJobLeaseLost) {
			t.Fatal("stale completion accepted", e)
		}
		if e = tx.Rollback(); e != nil {
			t.Fatal(e)
		}
		balance(9)
	})
	t.Run("successful completion holds ownership through commit and rollback", func(t *testing.T) {
		for _, commit := range []bool{true, false} {
			clean()
			handoff("go")
			publish()
			j := claim()
			tx, e := db.BeginTxx(ctx, nil)
			if e != nil {
				t.Fatal(e)
			}
			if _, e = tx.ExecContext(ctx, `UPDATE communications_communicationconfiguration SET daily_email_balance=0`); e != nil {
				t.Fatal(e)
			}
			if e = queue.CompleteInTx(ctx, tx, j.ID, j.LeaseToken, nil); e != nil {
				t.Fatal(e)
			}
			blocked, stop := context.WithTimeout(ctx, 80*time.Millisecond)
			_, e = db.ExecContext(blocked, `UPDATE assozeta_go_job_ownership SET runtime='django',generation=generation+1 WHERE singleton=1`)
			stop()
			if e == nil {
				tx.Rollback()
				t.Fatal("handoff crossed uncommitted completion")
			}
			if commit {
				e = tx.Commit()
			} else {
				e = tx.Rollback()
			}
			if e != nil {
				t.Fatal(e)
			}
			handoff("django")
			if commit {
				balance(0)
			} else {
				balance(9)
			}
		}
		clean()
	})
	t.Run("claim waits for concurrent handoff and rechecks owner", func(t *testing.T) {
		defer clean()
		handoff("go")
		j := publish()
		tx, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer tx.Rollback()
		if _, e = tx.ExecContext(ctx, `UPDATE assozeta_go_job_ownership SET runtime='django',generation=generation+1 WHERE singleton=1`); e != nil {
			t.Fatal(e)
		}
		started, done := make(chan struct{}), make(chan error, 1)
		go func() {
			close(started)
			jobs, err := queue.Claim(ctx, []string{j.Kind}, "racing-claim", 1, time.Minute)
			if err == nil && len(jobs) != 0 {
				err = errors.New("claim used pre-handoff snapshot")
			}
			done <- err
		}()
		<-started
		select {
		case err := <-done:
			t.Fatal("claim bypassed uncommitted ownership", err)
		case <-time.After(50 * time.Millisecond):
		}
		if e = tx.Commit(); e != nil {
			t.Fatal(e)
		}
		if e = <-done; e != nil {
			t.Fatal(e)
		}
		got, e := queue.Get(ctx, j.AssociationID, j.ID)
		if e != nil || got.Attempts != 0 {
			t.Fatal("racing claim spent attempts", e)
		}
	})
	t.Run("daily reset locks ownership before touching business rows", func(t *testing.T) {
		defer clean()
		handoff("go")
		publish()
		j := claim()
		business, e := db.BeginTxx(ctx, nil)
		if e != nil {
			t.Fatal(e)
		}
		defer business.Rollback()
		if _, e = business.ExecContext(ctx, `LOCK TABLE communications_communicationconfiguration IN ACCESS EXCLUSIVE MODE`); e != nil {
			t.Fatal(e)
		}
		done := make(chan error, 1)
		go func() { done <- reset.Run(ctx, j) }()
		deadline := time.Now().Add(5 * time.Second)
		waiting := false
		for time.Now().Before(deadline) {
			if e = db.GetContext(ctx, &waiting, `SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE 'UPDATE communications_communicationconfiguration SET daily_email_balance=0%')`); e != nil {
				t.Fatal(e)
			}
			if waiting {
				break
			}
			time.Sleep(5 * time.Millisecond)
		}
		if !waiting {
			t.Fatal("reset did not reach blocked business update")
		}
		blocked, stop := context.WithTimeout(ctx, 80*time.Millisecond)
		_, e = db.ExecContext(blocked, `UPDATE assozeta_go_job_ownership SET runtime='django',generation=generation+1 WHERE singleton=1`)
		stop()
		if e == nil {
			t.Fatal("ownership changed before admitted business effect")
		}
		if e = queue.Renew(ctx, j.ID, j.LeaseToken, time.Minute); e != nil {
			t.Fatal("effect fence blocked heartbeat", e)
		}
		if e = business.Rollback(); e != nil {
			t.Fatal(e)
		}
		if e = <-done; e != nil {
			t.Fatal(e)
		}
		handoff("django")
		balance(0)
	})
	t.Run("executor heartbeat cancels lost owner and stops intake", func(t *testing.T) {
		defer clean()
		handoff("go")
		publish()
		first := claim()
		// Return this fixture claim to pending; production uses fenced Fail for retries.
		exec(`UPDATE assozeta_go_jobs SET state='pending',lease_owner='',lease_token='00000000-0000-0000-0000-000000000000',lease_until=NULL WHERE id=$1`, first.ID)
		second := publish()
		entered := make(chan struct{})
		gate := NewJobRestoreAdmission(repository.NewRestoreFileLock(""), repository.NewRestoreStates(db), false)
		worker, e := NewJobExecutor(queue, gate, map[string]JobDefinition{domain.JobResetDailyEmailBalance: {Version: 1, Timeout: 5 * time.Second, Run: func(work context.Context, job domain.Job) error {
			close(entered)
			<-work.Done()
			return reset.Run(work, job)
		}}}, JobExecutorOptions{Owner: "ownership-executor", Concurrency: 1, Lease: time.Second, RenewInterval: 20 * time.Millisecond, StoreTimeout: 200 * time.Millisecond, PollInterval: time.Millisecond, MaintenanceInterval: time.Millisecond, DrainTimeout: time.Second})
		if e != nil {
			t.Fatal(e)
		}
		done := make(chan error, 1)
		go func() { done <- worker.Run(ctx) }()
		select {
		case <-entered:
		case <-time.After(5 * time.Second):
			t.Fatal("executor did not dispatch")
		}
		handoff("django")
		select {
		case e = <-done:
			if !errors.Is(e, domain.ErrJobLeaseLost) {
				t.Fatal("owner loss not reported", e)
			}
		case <-time.After(5 * time.Second):
			t.Fatal("worker retained lost ownership")
		}
		balance(9)
		got, e := queue.Get(ctx, second.AssociationID, second.ID)
		if e != nil || got.Attempts != 0 {
			t.Fatal("owner-lost worker claimed more work", e)
		}
	})
	t.Run("competing trusted fixture handoffs use generation compare and swap", func(t *testing.T) {
		handoff("django")
		var generation int64
		if e := db.GetContext(ctx, &generation, `SELECT generation FROM assozeta_go_job_ownership WHERE singleton=1`); e != nil {
			t.Fatal(e)
		}
		var group sync.WaitGroup
		results := make(chan int64, 2)
		for range 2 {
			group.Add(1)
			go func() {
				defer group.Done()
				result, e := db.ExecContext(ctx, `UPDATE assozeta_go_job_ownership SET runtime='go',generation=generation+1 WHERE singleton=1 AND generation=$1`, generation)
				if e != nil {
					t.Error(e)
					return
				}
				n, e := result.RowsAffected()
				if e != nil {
					t.Error(e)
					return
				}
				results <- n
			}()
		}
		group.Wait()
		close(results)
		var changed int64
		for n := range results {
			changed += n
		}
		if changed != 1 {
			t.Fatal("competing handoffs both succeeded", changed)
		}
	})
	t.Run("ownership waits never lock job rows before the owner", func(t *testing.T) {
		defer clean()
		for _, operation := range []string{"claim", "renew", "fail", "complete"} {
			clean()
			handoff("go")
			publish()
			job := claim()
			pending := publish()
			tx, e := db.BeginTxx(ctx, nil)
			if e != nil {
				t.Fatal(e)
			}
			if _, e = tx.ExecContext(ctx, `UPDATE assozeta_go_job_ownership SET runtime='django',generation=generation+1 WHERE singleton=1`); e != nil {
				tx.Rollback()
				t.Fatal(e)
			}
			done := make(chan error, 1)
			go func() {
				switch operation {
				case "claim":
					jobs, err := queue.Claim(ctx, []string{job.Kind}, "order-probe", 1, time.Minute)
					if err == nil && len(jobs) > 0 {
						err = errors.New("ownership changed but claim succeeded")
					}
					done <- err
				case "renew":
					done <- queue.Renew(ctx, job.ID, job.LeaseToken, time.Minute)
				case "fail":
					done <- queue.Fail(ctx, job.ID, job.LeaseToken, "retry", true, 0)
				case "complete":
					done <- queue.Complete(ctx, job.ID, job.LeaseToken, nil)
				}
			}()
			deadline := time.Now().Add(5 * time.Second)
			waiting := false
			for time.Now().Before(deadline) {
				if e = db.GetContext(ctx, &waiting, `SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE 'WITH ownership AS MATERIALIZED%')`); e != nil {
					tx.Rollback()
					t.Fatal(e)
				}
				if waiting {
					break
				}
				time.Sleep(5 * time.Millisecond)
			}
			if !waiting {
				tx.Rollback()
				t.Fatal(operation, "did not wait on owner")
			}
			_, e = tx.ExecContext(ctx, `SELECT id FROM assozeta_go_jobs WHERE id IN ($1,$2) FOR UPDATE NOWAIT`, job.ID, pending.ID)
			if e != nil {
				tx.Rollback()
				<-done
				t.Fatal(operation, "locked jobs before owner", e)
			}
			if e = tx.Commit(); e != nil {
				t.Fatal(e)
			}
			e = <-done
			if operation == "claim" && e != nil || operation != "claim" && !errors.Is(e, domain.ErrJobLeaseLost) {
				t.Fatal(operation, "did not reject ownership loss", e)
			}
		}
	})
	t.Run("missing owner row denies claims without losing queued work", func(t *testing.T) {
		defer clean()
		j := publish()
		exec(`DELETE FROM assozeta_go_job_ownership`)
		jobs, e := queue.Claim(ctx, []string{j.Kind}, "missing", 1, time.Minute)
		if e != nil || len(jobs) != 0 {
			t.Fatal("missing ownership admitted", e)
		}
		got, e := queue.Get(ctx, j.AssociationID, j.ID)
		if e != nil || got.Attempts != 0 {
			t.Fatal("missing owner changed job", e)
		}
	})
}
