//go:build jobsintegration

package service

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/migrations"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

func TestJobRestoreAdmissionDjango(t *testing.T) {
	path := os.Getenv("ASSOZETA_JOB_ADMISSION_FIXTURE")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal("run accounts.py --only restore-admission")
	}
	var cases []struct {
		Name string
		Rows []struct {
			State     string
			Completed *time.Time
		}
		Expected                        string
		Configured, Locked, Unavailable bool
		Published                       time.Time
	}
	if err = json.Unmarshal(raw, &cases); err != nil || len(cases) != 13 {
		t.Fatal("missing declared job admission cases")
	}
	dsn := os.Getenv("ASSOZETA_JOB_TEST_DSN")
	db, err := sqlx.Connect("pgx", dsn)
	if err != nil {
		t.Fatal("private database unavailable")
	}
	defer db.Close()
	db.SetMaxOpenConns(4)
	if err = migrations.Apply(dsn); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	scope := uuid.MustParse("00000000-0000-4000-8000-000000088399")
	ids := []string{"00000000-0000-4000-8000-000000088300", "00000000-0000-4000-8000-000000088301"}
	var collision int
	if err = db.GetContext(ctx, &collision, `SELECT count(*) FROM assozeta_go_jobs WHERE association_id=$1`, scope); err != nil || collision != 0 {
		t.Fatal("job fixture scope collides")
	}
	queue := repository.NewJobs(db)
	cleanup := func() {
		if _, e := db.ExecContext(ctx, `DELETE FROM assozeta_go_jobs WHERE association_id=$1`, scope); e != nil {
			t.Error(e)
		}
	}
	defer cleanup()
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			cleanup()
			if _, e := db.ExecContext(ctx, `UPDATE instance_datarestore SET state='review',completed_at=NULL WHERE id=ANY($1::uuid[])`, ids); e != nil {
				t.Fatal(e)
			}
			for i, row := range c.Rows {
				if _, e := db.ExecContext(ctx, `UPDATE instance_datarestore SET state=$2,completed_at=$3 WHERE id=$1`, ids[i], row.State, row.Completed); e != nil {
					t.Fatal(e)
				}
			}
			lockPath := filepath.Join(t.TempDir(), "restore.lock")
			if !c.Configured {
				lockPath = ""
			}
			if c.Unavailable {
				lockPath = t.TempDir()
			}
			lock := repository.NewRestoreFileLock(lockPath)
			if c.Locked {
				exclusive, e := lock.Acquire(ctx, true, true)
				if e != nil {
					t.Fatal(e)
				}
				defer exclusive.Close()
			}
			tx, e := db.BeginTxx(ctx, nil)
			if e != nil {
				t.Fatal(e)
			}
			defer tx.Rollback()
			job, e := queue.Enqueue(ctx, tx, domain.JobIntent{ID: uuid.New(), AssociationID: scope, Kind: "contract.restoreprobe", Version: 1, Key: c.Name, Payload: json.RawMessage(`{}`), MaxAttempts: 3})
			if e != nil {
				t.Fatal(e)
			}
			if _, e = tx.ExecContext(ctx, `UPDATE assozeta_go_jobs SET created_at=$2 WHERE id=$1`, job.ID, c.Published); e != nil {
				t.Fatal(e)
			}
			if e = tx.Commit(); e != nil {
				t.Fatal(e)
			}
			called := false
			worker, e := NewJobExecutor(queue, NewJobRestoreAdmission(lock, repository.NewRestoreStates(db), c.Configured), map[string]JobDefinition{"contract.restoreprobe": {Version: 1, Timeout: time.Second, Run: func(ctx context.Context, j domain.Job) error {
				called = true
				return queue.Complete(ctx, j.ID, j.LeaseToken, nil)
			}}}, JobExecutorOptions{Owner: "fixture", Concurrency: 1, Lease: time.Second, RenewInterval: 100 * time.Millisecond, StoreTimeout: 200 * time.Millisecond, PollInterval: time.Millisecond, MaintenanceInterval: 15 * time.Second, DrainTimeout: time.Second})
			if e != nil {
				t.Fatal(e)
			}
			ran, e := worker.ExecuteNext(ctx)
			got, readErr := queue.Get(ctx, scope, job.ID)
			if readErr != nil {
				t.Fatal(readErr)
			}
			switch c.Expected {
			case "executed":
				if e != nil || !ran || !called || got.State != "completed" {
					t.Fatal("admitted task changed", e)
				}
			case "stale":
				if e != nil || !ran || called || got.State != "failed" || got.ErrorCode != "stale_after_restore" {
					t.Fatal("stale task had effects or wrong result", e)
				}
			case "deferred":
				if ran || called || got.Attempts != 0 || got.State != "pending" || !(errors.Is(e, domain.ErrRestoreBusy) || errors.Is(e, domain.ErrRestorePending) || errors.Is(e, domain.ErrRestoreVolumeUnavailable)) {
					t.Fatal("maintenance spent retry budget or had effects", e)
				}
			default:
				t.Fatal("unknown expected outcome")
			}
		})
	}
}
