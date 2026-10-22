//go:build transactionlifetimeintegration

package repository_test

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/middleware"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/testutil/transactionbarrier"
	"github.com/carbogninalberto/assozeta/BE_GO/migrations"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
	"github.com/labstack/echo/v4"
)

// The shared driver fixture supplies these channels. It pauses real pgx
// Rollback before delegation and physical connection cleanup before completion.
// Business transaction wrappers below only choose the cancellation boundary.
type lifetimeBarriers struct {
	rollbackEntered, cleanupEntered <-chan struct{}
	releaseRollback, releaseCleanup func()
}

func newLifetimeBarrierDB(t *testing.T, cfg *pgx.ConnConfig) (*sqlx.DB, lifetimeBarriers) {
	t.Helper()
	db, control, err := transactionbarrier.OpenConfig(cfg)
	if err != nil {
		t.Fatal(err)
	}
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	rollback, cleanup := control.ArmRollback(), control.ArmClose()
	return db, lifetimeBarriers{rollbackEntered: rollback.Entered, cleanupEntered: cleanup.Entered,
		releaseRollback: rollback.Release, releaseCleanup: cleanup.Release}
}

func TestTransactionLifetimePostgres(t *testing.T) {
	dsn := os.Getenv("ASSOZETA_TRANSACTION_LIFETIME_DSN")
	if dsn == "" {
		t.Fatal("run the private transaction_lifetime.py adapter")
	}
	cfg, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal("invalid private fixture DSN")
	}
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec
	admin := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	defer admin.Close()
	ctx, stop := context.WithTimeout(context.Background(), 90*time.Second)
	defer stop()
	var database string
	if err := admin.GetContext(ctx, &database, `SELECT current_database()`); err != nil || database != "assozeta_contract_tx_lifetime" {
		t.Fatal("wrong disposable database")
	}
	if err := migrations.Apply(dsn); err != nil {
		t.Fatal(err)
	}
	// Explicit synthetic sentinels exercise transaction lifetime, not historical
	// Django schema or business parity (covered by separate required adapters).
	for _, statement := range []string{
		`CREATE TABLE instance_datarestore(state text, completed_at timestamptz)`,
		`CREATE TABLE communications_communicationconfiguration(id integer PRIMARY KEY, daily_email_balance integer)`,
		`INSERT INTO communications_communicationconfiguration VALUES(1,7)`,
		`CREATE TABLE bakney_user(user_id uuid PRIMARY KEY, first_name text, last_name text, deleted boolean, tables_settings jsonb, temporary_invoice_deletion boolean)`,
		`INSERT INTO bakney_user VALUES('00000000-0000-4000-8000-000000000001','Fixture','Owner',false,'{}',false)`,
		`UPDATE assozeta_go_job_ownership SET runtime='go',generation=generation+1 WHERE singleton=1`,
	} {
		if _, err := admin.ExecContext(ctx, statement); err != nil {
			t.Fatal(err)
		}
	}
	const owner = "00000000-0000-4000-8000-000000000001"
	for _, name := range []string{
		"worker daily reset retains admission through rollback and cleanup",
		"HTTP table mutation retains admission through rollback and cleanup",
		"read only snapshot joins rollback and cleanup before returning",
	} {
		t.Run(name, func(t *testing.T) {
			work, cancel := context.WithCancel(ctx)
			defer cancel()
			local := cfg.Copy()
			if strings.HasPrefix(name, "read only") {
				local.Tracer = &lifetimeCancelRead{cancel: cancel}
			}
			db, barriers := newLifetimeBarrierDB(t, local)
			defer db.Close()
			defer barriers.releaseCleanup()
			defer barriers.releaseRollback()
			locks := repository.NewRestoreFileLock(filepath.Join(t.TempDir(), "lifecycle.lock"))
			done := make(chan struct{})
			var runErr error
			var run func() error
			var lockSQL string
			var workerJob uuid.UUID
			switch {
			case strings.HasPrefix(name, "worker"):
				queue := repository.NewJobs(admin)
				tx, err := admin.BeginTxx(ctx, nil)
				if err != nil {
					t.Fatal(err)
				}
				defer tx.Rollback()
				workerJob = uuid.New()
				_, err = queue.Enqueue(ctx, tx, domain.JobIntent{ID: workerJob, Kind: domain.JobResetDailyEmailBalance, Version: 1, Key: uuid.NewString(), Payload: []byte(`{}`), MaxAttempts: 2})
				if err != nil {
					t.Fatal(err)
				}
				if err = tx.Commit(); err != nil {
					t.Fatal(err)
				}
				store := &lifetimeResetStore{EmailBalanceResetStore: repository.NewEmailBalanceResets(db), cancel: cancel, entered: barriers.rollbackEntered}
				reset := service.NewEmailBalanceReset(store, slog.New(slog.NewTextHandler(io.Discard, nil)))
				executor, err := service.NewJobExecutor(queue, service.NewJobRestoreAdmission(locks, repository.NewRestoreStates(admin), true), map[string]service.JobDefinition{
					domain.JobResetDailyEmailBalance: {Version: 1, Timeout: 20 * time.Second, Run: reset.Run},
				}, service.JobExecutorOptions{Owner: "lifetime-fixture", Concurrency: 1, Lease: time.Minute, RenewInterval: 10 * time.Second, StoreTimeout: time.Second, PollInterval: time.Second, MaintenanceInterval: 15 * time.Second, DrainTimeout: time.Second})
				if err != nil {
					t.Fatal(err)
				}
				run = func() error { _, err := executor.ExecuteNext(work); return err }
				lockSQL = `SELECT id FROM communications_communicationconfiguration WHERE id=1 FOR UPDATE NOWAIT`
			case strings.HasPrefix(name, "HTTP"):
				store := &lifetimeTablesStore{ProfileTablesStore: repository.NewProfileTables(db), cancel: cancel, entered: barriers.rollbackEntered}
				flow := service.NewProfileTables(store, time.Now)
				person := &domain.Account{ID: owner, Role: domain.RoleAssociation}
				identity := domain.ResolvedIdentity{Actor: person, Effective: person, ScopeUser: person}
				e := echo.New()
				wrapped := middleware.DjangoRestoreAdmission(service.NewRestoreAdmission(locks, repository.NewRestoreStates(admin), true))(func(c echo.Context) error {
					return flow.Update(c.Request().Context(), identity, dto.ProfileTablesRequest{Present: true, Value: domain.RequestObject{{Name: "fixture", Value: true}}}, domain.LoginAuditContext{})
				})
				run = func() error {
					req := httptest.NewRequest(http.MethodPost, "/profile/settings/tables", nil).WithContext(work)
					return wrapped(e.NewContext(req, httptest.NewRecorder()))
				}
				lockSQL = `SELECT user_id FROM bakney_user WHERE user_id='` + owner + `' FOR UPDATE NOWAIT`
			default:
				run = func() error {
					_, err := repository.NewLoginResponseSettings(db).LoginResponseSettings(work, owner, false)
					return err
				}
				lockSQL = `LOCK TABLE bakney_user IN ACCESS EXCLUSIVE MODE NOWAIT`
			}
			go func() { defer close(done); runErr = run() }()
			defer func() {
				cancel()
				barriers.releaseRollback()
				barriers.releaseCleanup()
				lifetimeAwait(t, done, "caller cleanup")
			}()
			lifetimeAwait(t, barriers.rollbackEntered, "driver rollback")
			lifetimeAssertSQLBusy(t, admin, lockSQL)
			lifetimeAssertPending(t, done, "driver rollback")
			if !strings.HasPrefix(name, "read only") {
				lifetimeAssertLeaseBusy(t, locks)
			}
			barriers.releaseRollback()
			lifetimeAwait(t, barriers.cleanupEntered, "physical connection cleanup")
			lifetimeAssertPending(t, done, "physical cleanup")
			if !strings.HasPrefix(name, "read only") {
				lifetimeAssertLeaseBusy(t, locks)
			}
			barriers.releaseCleanup()
			lifetimeAwait(t, done, "completed caller")
			if workerJob != uuid.Nil {
				job, err := repository.NewJobs(admin).Get(ctx, uuid.Nil, workerJob)
				if err != nil || job.State != "pending" || job.ErrorCode != "worker_shutdown" {
					t.Error("cancelled worker was not recoverable", job.State, err)
				}
			} else if runErr == nil {
				t.Error("cancelled work reported success")
			}
			if !strings.HasPrefix(name, "read only") {
				lease, err := locks.Acquire(ctx, true, true)
				if err != nil {
					t.Fatal("finished work retained admission", err)
				}
				_ = lease.Close()
			}
			var balance int
			var preferences string
			if err := admin.GetContext(ctx, &balance, `SELECT daily_email_balance FROM communications_communicationconfiguration WHERE id=1`); err != nil || balance != 7 {
				t.Fatal("business rollback", balance, err)
			}
			if err := admin.GetContext(ctx, &preferences, `SELECT tables_settings::text FROM bakney_user WHERE user_id=$1`, owner); err != nil || preferences != "{}" {
				t.Fatal("preferences rollback", preferences, err)
			}
		})
	}
}

func lifetimeAwait(t *testing.T, ch <-chan struct{}, label string) {
	t.Helper()
	select {
	case <-ch:
	case <-time.After(10 * time.Second):
		t.Fatal("timed out waiting for", label)
	}
}
func lifetimeAssertPending(t *testing.T, done <-chan struct{}, label string) {
	t.Helper()
	select {
	case <-done:
		t.Error("caller returned before", label)
	case <-time.After(100 * time.Millisecond):
	}
}
func lifetimeAssertSQLBusy(t *testing.T, db *sqlx.DB, query string) {
	t.Helper()
	tx, err := db.BeginTxx(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback()
	_, err = tx.Exec(query)
	var pg *pgconn.PgError
	if !errors.As(err, &pg) || pg.Code != "55P03" {
		t.Error("expected live PostgreSQL transaction lock", err)
	}
}
func lifetimeAssertLeaseBusy(t *testing.T, locks repository.RestoreOperationLocker) {
	t.Helper()
	lease, err := locks.Acquire(context.Background(), true, true)
	if lease != nil {
		_ = lease.Close()
	}
	if !errors.Is(err, domain.ErrRestoreBusy) {
		t.Error("lifecycle lease escaped unfinished cleanup", err)
	}
}

type lifetimeResetStore struct {
	domain.EmailBalanceResetStore
	cancel  context.CancelFunc
	entered <-chan struct{}
}

func (s *lifetimeResetStore) BeginEmailBalanceReset(ctx context.Context, job domain.Job) (domain.EmailBalanceResetTransaction, error) {
	tx, err := s.EmailBalanceResetStore.BeginEmailBalanceReset(ctx, job)
	if err != nil {
		return nil, err
	}
	return &lifetimeResetTransaction{EmailBalanceResetTransaction: tx, store: s}, nil
}

type lifetimeResetTransaction struct {
	domain.EmailBalanceResetTransaction
	store *lifetimeResetStore
}

func (t *lifetimeResetTransaction) ResetPositiveCounters(ctx context.Context) (int64, error) {
	n, err := t.EmailBalanceResetTransaction.ResetPositiveCounters(ctx)
	if err != nil {
		return n, err
	}
	t.store.cancel()
	<-t.store.entered
	return n, ctx.Err()
}

type lifetimeTablesStore struct {
	domain.ProfileTablesStore
	cancel  context.CancelFunc
	entered <-chan struct{}
}

func (s *lifetimeTablesStore) BeginProfileTables(ctx context.Context) (domain.ProfileTablesTransaction, error) {
	tx, err := s.ProfileTablesStore.BeginProfileTables(ctx)
	if err != nil {
		return nil, err
	}
	return &lifetimeTablesTransaction{ProfileTablesTransaction: tx, store: s}, nil
}

type lifetimeTablesTransaction struct {
	domain.ProfileTablesTransaction
	store *lifetimeTablesStore
}

func (t *lifetimeTablesTransaction) SetTablePreferences(ctx context.Context, id string, value json.RawMessage) error {
	if err := t.ProfileTablesTransaction.SetTablePreferences(ctx, id, value); err != nil {
		return err
	}
	t.store.cancel()
	<-t.store.entered
	return ctx.Err()
}

type lifetimeCancelRead struct {
	cancel context.CancelFunc
	once   sync.Once
}

func (*lifetimeCancelRead) TraceQueryStart(ctx context.Context, _ *pgx.Conn, _ pgx.TraceQueryStartData) context.Context {
	return ctx
}
func (t *lifetimeCancelRead) TraceQueryEnd(_ context.Context, _ *pgx.Conn, data pgx.TraceQueryEndData) {
	if data.Err == nil && strings.HasPrefix(data.CommandTag.String(), "SELECT") {
		t.once.Do(t.cancel)
	}
}
