//go:build finalizationintegration

package service

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jmoiron/sqlx"
)

type finalizationQueries struct{ count atomic.Int64 }

func (q *finalizationQueries) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	sql := strings.ToLower(strings.TrimSpace(d.SQL))
	if strings.HasPrefix(sql, "select") || strings.HasPrefix(sql, "insert") || strings.HasPrefix(sql, "update") {
		q.count.Add(1)
	}
	return ctx
}
func (*finalizationQueries) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}
func TestLoginFinalizationPostgres(t *testing.T) {
	path := os.Getenv("ASSOZETA_LOGIN_FINALIZATION_FIXTURE")
	if path == "" {
		t.Fatal("run TESTS/adapters/django/accounts.py")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Cases []struct {
			Name, ID, First, Second string
			Clear                   bool
			Association             *string
			Audit                   domain.LoginAuditContext
		}
		Atomic string
	}
	if json.Unmarshal(data, &fixture) != nil || len(fixture.Cases) == 0 || fixture.Atomic == "" {
		t.Fatal("invalid finalization fixture")
	}
	cfg, err := pgx.ParseConfig(os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal(err)
	}
	counter := &finalizationQueries{}
	cfg.Tracer = counter
	db := sqlx.NewDb(ownedpgx.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	store := repository.NewLoginFinalizations(db)
	at := time.Date(2026, 10, 21, 12, 0, 0, 0, time.UTC)
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			first, err := time.Parse(time.RFC3339Nano, tc.First)
			if err != nil {
				t.Fatal(err)
			}
			second, err := time.Parse(time.RFC3339Nano, tc.Second)
			if err != nil {
				t.Fatal(err)
			}
			calls := 0
			clock := func() time.Time {
				calls++
				if calls == 1 {
					return first
				}
				return second
			}
			counter.count.Store(0)
			if err := NewLoginFinalization(store, clock).Finalize(context.Background(), tc.ID, tc.Audit); err != nil {
				t.Fatal(err)
			}
			// The shared server fence adds one SELECT to every business path.
			want := int64(3)
			if tc.Clear {
				want = 5
				if tc.Association != nil {
					want = 6
				}
			}
			if counter.count.Load() != want {
				t.Fatalf("queries %d want %d", counter.count.Load(), want)
			}
		})
	}
	reset := func() {
		t.Helper()
		if _, err := db.Exec(`DELETE FROM application_auditlogindex WHERE log_entry_id IN (SELECT id FROM auditlog_logentry WHERE object_pk=$1)`, fixture.Atomic); err != nil {
			t.Fatal(err)
		}
		if _, err := db.Exec(`DELETE FROM auditlog_logentry WHERE object_pk=$1`, fixture.Atomic); err != nil {
			t.Fatal(err)
		}
		if _, err := db.Exec(`UPDATE bakney_user SET last_login=NULL,delete_on='2026-10-21' WHERE user_id=$1`, fixture.Atomic); err != nil {
			t.Fatal(err)
		}
	}
	pristine := func() {
		t.Helper()
		var clean bool
		if err := db.Get(&clean, `SELECT last_login IS NULL AND delete_on='2026-10-21' AND NOT EXISTS (SELECT 1 FROM auditlog_logentry WHERE object_pk=$1) FROM bakney_user WHERE user_id=$1::uuid`, fixture.Atomic); err != nil || !clean {
			t.Fatalf("failed transaction retained mutation/audit: %v", err)
		}
		if db.Stats().InUse != 0 {
			t.Fatal("transaction retained connection")
		}
	}
	service := NewLoginFinalization(store, func() time.Time { return at })
	t.Run("concurrent cancellation audits exactly once", func(t *testing.T) {
		counter.count.Store(0)
		var wg sync.WaitGroup
		for i := 0; i < 32; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
				defer cancel()
				if err := service.Finalize(ctx, fixture.Atomic, domain.LoginAuditContext{}); err != nil {
					t.Error(err)
				}
			}()
		}
		wg.Wait()
		if counter.count.Load() != 99 || db.Stats().InUse != 0 {
			t.Fatalf("query bound/pool: %d", counter.count.Load())
		}
		var n int
		if err := db.Get(&n, `SELECT count(*) FROM auditlog_logentry WHERE object_pk=$1`, fixture.Atomic); err != nil || n != 1 {
			t.Fatal("cancellation duplicated audit", err)
		}
		var done bool
		if err := db.Get(&done, `SELECT last_login=$2 AND delete_on IS NULL FROM bakney_user WHERE user_id=$1`, fixture.Atomic, at); err != nil || !done {
			t.Fatal("concurrent final state wrong", err)
		}
		reset()
	})
	t.Run("every failed write rolls back", func(t *testing.T) {
		for _, mode := range []string{"after_write", "after_audit", "actor_fk", "index_fk", "before_commit", "cancel"} {
			t.Run(mode, func(t *testing.T) {
				ctx, cancel := context.WithCancel(context.Background())
				defer cancel()
				wrapped := &failingFinalizationStore{LoginFinalizationStore: store, mode: mode, cancel: cancel}
				err := NewLoginFinalization(wrapped, func() time.Time { return at }).Finalize(ctx, fixture.Atomic, domain.LoginAuditContext{})
				if err == nil {
					t.Fatal("failure returned success")
				}
				if mode == "actor_fk" || mode == "index_fk" {
					var pg *pgconn.PgError
					if !errors.As(err, &pg) || pg.Code != "23503" {
						t.Fatalf("expected actual FK failure: %v", err)
					}
				}
				if mode == "cancel" && !errors.Is(err, context.Canceled) {
					t.Fatal("cancellation lost", err)
				}
				pristine()
			})
		}
	})
	t.Run("actual database permission failures roll back", func(t *testing.T) {
		for _, role := range []string{"final_user_denied", "final_index_denied"} {
			t.Run(role, func(t *testing.T) {
				restricted := cfg.Copy()
				restricted.RuntimeParams["role"] = role
				roleDB := sqlx.NewDb(ownedpgx.OpenDB(*restricted), "pgx")
				defer roleDB.Close()
				roleDB.SetMaxOpenConns(1)
				err := NewLoginFinalization(repository.NewLoginFinalizations(roleDB), func() time.Time { return at }).Finalize(context.Background(), fixture.Atomic, domain.LoginAuditContext{})
				var pg *pgconn.PgError
				if !errors.As(err, &pg) || pg.Code != "42501" {
					t.Fatalf("expected permission denial: %v", err)
				}
				if roleDB.Stats().InUse != 0 {
					t.Fatal("denial retained connection")
				}
				pristine()
			})
		}
	})
	t.Run("missing account and cancelled request", func(t *testing.T) {
		if err := service.Finalize(context.Background(), "ffffffff-ffff-4fff-8fff-ffffffffffff", domain.LoginAuditContext{}); !errors.Is(err, domain.ErrAccountNotFound) {
			t.Fatal("missing account not denied", err)
		}
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		if err := service.Finalize(ctx, fixture.Atomic, domain.LoginAuditContext{}); !errors.Is(err, context.Canceled) {
			t.Fatal("cancelled request not denied", err)
		}
		pristine()
	})
	t.Run("fresh fields survive finalization", func(t *testing.T) {
		var original struct {
			Password  string `db:"password"`
			FirstName string `db:"first_name"`
			Active    bool   `db:"is_active"`
		}
		if err := db.Get(&original, `SELECT password,first_name,is_active FROM bakney_user WHERE user_id=$1`, fixture.Atomic); err != nil {
			t.Fatal(err)
		}
		defer func() {
			reset()
			if _, err := db.Exec(`UPDATE bakney_user SET password=$2,first_name=$3,is_active=$4 WHERE user_id=$1`, fixture.Atomic, original.Password, original.FirstName, original.Active); err != nil {
				t.Error(err)
			}
		}()
		if _, err := db.Exec(`UPDATE bakney_user SET password='!changed-concurrently',first_name='Fresh',is_active=false WHERE user_id=$1`, fixture.Atomic); err != nil {
			t.Fatal(err)
		}
		if err := service.Finalize(context.Background(), fixture.Atomic, domain.LoginAuditContext{}); err != nil {
			t.Fatal(err)
		}
		var preserved bool
		if err := db.Get(&preserved, `SELECT password='!changed-concurrently' AND first_name='Fresh' AND NOT is_active FROM bakney_user WHERE user_id=$1`, fixture.Atomic); err != nil || !preserved {
			t.Fatal("finalization overwrote unrelated state", err)
		}
		var repr string
		if err := db.Get(&repr, `SELECT object_repr FROM auditlog_logentry WHERE object_pk=$1`, fixture.Atomic); err != nil || repr != "name: Fresh Last" {
			t.Fatal("audit used stale user state", err)
		}
	})
}

type failingFinalizationStore struct {
	domain.LoginFinalizationStore
	mode   string
	cancel context.CancelFunc
}

func (s *failingFinalizationStore) BeginLoginFinalization(ctx context.Context) (domain.LoginFinalizationTransaction, error) {
	tx, err := s.LoginFinalizationStore.BeginLoginFinalization(ctx)
	if err != nil {
		return nil, err
	}
	return &failingFinalizationTx{LoginFinalizationTransaction: tx, mode: s.mode, cancel: s.cancel}, nil
}

type failingFinalizationTx struct {
	domain.LoginFinalizationTransaction
	mode   string
	cancel context.CancelFunc
}

func (t *failingFinalizationTx) SetLoginTime(ctx context.Context, id string, at time.Time, clear bool) error {
	err := t.LoginFinalizationTransaction.SetLoginTime(ctx, id, at, clear)
	if err == nil && t.mode == "after_write" {
		return errors.New("failure after actual update")
	}
	return err
}
func (t *failingFinalizationTx) AppendLoginDeletionAudit(ctx context.Context, event domain.LoginDeletionAudit) error {
	ghost := "ffffffff-ffff-4fff-8fff-ffffffffffff"
	if t.mode == "actor_fk" {
		event.Context.ActorID = &ghost
	}
	if t.mode == "index_fk" {
		event.AssociationID = &ghost
	}
	err := t.LoginFinalizationTransaction.AppendLoginDeletionAudit(ctx, event)
	if err != nil {
		return err
	}
	if t.mode == "after_audit" {
		return errors.New("failure after actual audit")
	}
	if t.mode == "cancel" {
		t.cancel()
	}
	return nil
}
func (t *failingFinalizationTx) Commit() error {
	if t.mode == "before_commit" {
		return errors.New("commit rejected")
	}
	return t.LoginFinalizationTransaction.Commit()
}
