//go:build cardconfigurationintegration

package service

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

type cardQueries struct{ count atomic.Int64 }

func (q *cardQueries) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	sql := strings.ToLower(strings.TrimSpace(d.SQL))
	if strings.HasPrefix(sql, "select") || strings.HasPrefix(sql, "insert") || strings.HasPrefix(sql, "update") {
		q.count.Add(1)
	}
	return ctx
}
func (*cardQueries) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

func TestMembershipCardConfigurationAgainstDjango(t *testing.T) {
	path := os.Getenv("ASSOZETA_CARD_CONFIGURATION_FIXTURE")
	if path == "" {
		t.Fatal("run TESTS/adapters/django/accounts.py")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Cases []struct {
			Name, ID, Owner, Association, First, Outcome string
			Role, Count                                  int
			Superuser, Created                           bool
			Expected                                     json.RawMessage
			Audit                                        domain.LoginAuditContext
		}
	}
	if json.Unmarshal(data, &fixture) != nil || len(fixture.Cases) == 0 {
		t.Fatal("invalid configuration fixture")
	}
	cfg, err := pgx.ParseConfig(os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal(err)
	}
	counter := &cardQueries{}
	cfg.Tracer = counter
	db := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	store := repository.NewMembershipCardConfigurations(db)
	service := NewMembershipCardConfigurations(store, time.Now)
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			account := domain.Account{ID: tc.ID, Role: tc.Role, Superuser: tc.Superuser}
			if tc.Role == domain.RoleCollaborator {
				account.ConnectedUserID = &tc.Owner
			}
			counter.count.Store(0)
			result, err := service.ForLogin(context.Background(), account, tc.Association, tc.Audit)
			wantQueries := 3 // two business reads plus the shared server fence
			switch {
			case tc.Outcome == "missing":
				wantQueries = 2
				if result != nil || !errors.Is(err, domain.ErrAssociationNotFound) {
					t.Fatalf("missing association produced configuration: %v", err)
				}
			case tc.Superuser || tc.Role == domain.RoleAthlete:
				wantQueries = 0
				if result != nil || err != nil {
					t.Fatal("non-association response touched configuration")
				}
			default:
				if err != nil || result == nil {
					t.Fatalf("configuration unavailable: %v", err)
				}
				actual, err := json.Marshal(result)
				if err != nil {
					t.Fatal(err)
				}
				var a, b any
				da, db := json.NewDecoder(bytes.NewReader(actual)), json.NewDecoder(bytes.NewReader(tc.Expected))
				da.UseNumber()
				db.UseNumber()
				if da.Decode(&a) != nil || db.Decode(&b) != nil || !reflect.DeepEqual(a, b) {
					t.Fatalf("configuration mismatch: got %s want %s", actual, tc.Expected)
				}
				if tc.Created {
					wantQueries = 6
				}
			}
			if counter.count.Load() != int64(wantQueries) {
				t.Fatalf("query count %d want %d", counter.count.Load(), wantQueries)
			}
		})
	}
	t.Run("concurrent creation reuses one configuration and audit", func(t *testing.T) {
		tc := fixture.Cases[0]
		// Restore this already-checked creation before racing the same operation.
		deleteCardFixture(t, db, tc.Association)
		counter.count.Store(0)
		var wg sync.WaitGroup
		for i := 0; i < 32; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
				defer cancel()
				result, err := service.ForLogin(ctx, domain.Account{ID: tc.ID, Role: 1}, tc.Association, tc.Audit)
				if err != nil || result == nil || result.EmitOnlyOnApproval {
					t.Error("concurrent default creation failed")
				}
			}()
		}
		wg.Wait()
		if counter.count.Load() != 99 || db.Stats().InUse != 0 {
			t.Fatalf("query bound/pool release: %d", counter.count.Load())
		}
		var count int
		if err := db.Get(&count, `SELECT count(*) FROM application_sportassociationmembershipcardconfiguration WHERE sport_association_id=$1`, tc.Association); err != nil || count != 1 {
			t.Fatal("duplicate configuration created")
		}
		if err := db.Get(&count, `SELECT count(*) FROM application_auditlogindex WHERE sport_association_id=$1`, tc.Association); err != nil || count != 1 {
			t.Fatal("duplicate audit index created")
		}
	})
	t.Run("configuration audit invariant rolls back every failed write", func(t *testing.T) {
		tc := fixture.Cases[0]
		deleteCardFixture(t, db, tc.Association)
		defer func() {
			if _, err := service.ForLogin(context.Background(), domain.Account{ID: tc.ID, Role: 1}, tc.Association, tc.Audit); err != nil {
				t.Error("fixture restore failed", err)
			}
		}()
		for _, role := range []string{"card_log_denied", "card_index_denied"} {
			t.Run(role, func(t *testing.T) {
				deniedCfg := cfg.Copy()
				deniedCfg.RuntimeParams["role"] = role
				deniedDB := sqlx.NewDb(stdlib.OpenDB(*deniedCfg), "pgx")
				defer deniedDB.Close()
				deniedDB.SetMaxOpenConns(1)
				denied := NewMembershipCardConfigurations(repository.NewMembershipCardConfigurations(deniedDB), time.Now)
				result, err := denied.ForLogin(context.Background(), domain.Account{ID: tc.ID, Role: 1}, tc.Association, tc.Audit)
				var sqlError *pgconn.PgError
				if result != nil || !errors.As(err, &sqlError) || sqlError.Code != "42501" {
					t.Fatalf("permission failure not exercised: %v", err)
				}
				var count int
				if err := db.Get(&count, `SELECT count(*) FROM application_sportassociationmembershipcardconfiguration WHERE sport_association_id=$1`, tc.Association); err != nil || count != 0 {
					t.Fatal("permission failure retained configuration")
				}
				if err := db.Get(&count, `SELECT count(*) FROM auditlog_logentry WHERE content_type_id=(SELECT id FROM django_content_type WHERE app_label='application' AND model='sportassociationmembershipcardconfiguration') AND changes->'sport_association'->>1=$1`, tc.Association); err != nil || count != 0 {
					t.Fatal("permission failure retained audit")
				}
				if deniedDB.Stats().InUse != 0 {
					t.Fatal("permission failure leaked transaction")
				}
			})
		}
		for _, mode := range []string{"after_insert", "after_audit", "actor_fk", "index_fk", "before_commit", "cancel"} {
			t.Run(mode, func(t *testing.T) {
				ctx, cancel := context.WithCancel(context.Background())
				defer cancel()
				failed := NewMembershipCardConfigurations(&failedCardStore{MembershipCardConfigurationStore: store, mode: mode, cancel: cancel}, time.Now)
				result, err := failed.ForLogin(ctx, domain.Account{ID: tc.ID, Role: 1}, tc.Association, tc.Audit)
				if err == nil || result != nil {
					t.Fatal("failed creation returned configuration")
				}
				var count int
				if err := db.Get(&count, `SELECT count(*) FROM application_sportassociationmembershipcardconfiguration WHERE sport_association_id=$1`, tc.Association); err != nil || count != 0 {
					t.Fatal("failed creation retained configuration")
				}
				if err := db.Get(&count, `SELECT count(*) FROM auditlog_logentry WHERE content_type_id=(SELECT id FROM django_content_type WHERE app_label='application' AND model='sportassociationmembershipcardconfiguration') AND changes->'sport_association'->>1=$1`, tc.Association); err != nil || count != 0 {
					t.Fatal("failed creation retained audit")
				}
				if db.Stats().InUse != 0 {
					t.Fatal("failed creation leaked transaction")
				}
			})
		}
	})
	t.Run("real audit actor deadlock retries the configuration transaction", func(t *testing.T) {
		tc := fixture.Cases[0]
		deleteCardFixture(t, db, tc.Association)
		defer func() {
			deleteCardFixture(t, db, tc.Association)
			if _, err := service.ForLogin(context.Background(), domain.Account{ID: tc.ID, Role: 1}, tc.Association, tc.Audit); err != nil {
				t.Error(err)
			}
		}()
		var audit domain.LoginAuditContext
		for _, entry := range fixture.Cases {
			if entry.Audit.ActorID != nil {
				audit = entry.Audit
				break
			}
		}
		if audit.ActorID == nil {
			t.Fatal("missing audit actor fixture")
		}
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		blocker, err := db.BeginTxx(ctx, nil)
		if err != nil {
			t.Fatal(err)
		}
		defer blocker.Rollback()
		if _, err := blocker.ExecContext(ctx, `SET LOCAL deadlock_timeout='30s'`); err != nil {
			t.Fatal(err)
		}
		var blockerPID int
		if err := blocker.GetContext(ctx, &blockerPID, `SELECT pg_backend_pid()`); err != nil {
			t.Fatal(err)
		}
		var actor string
		if err := blocker.GetContext(ctx, &actor, `SELECT user_id FROM bakney_user WHERE user_id=$1 FOR UPDATE`, *audit.ActorID); err != nil {
			t.Fatal(err)
		}
		retryCfg := cfg.Copy()
		retryCfg.RuntimeParams["deadlock_timeout"] = "10ms"
		retryDB := sqlx.NewDb(stdlib.OpenDB(*retryCfg), "pgx")
		defer retryDB.Close()
		retryDB.SetMaxOpenConns(2)
		observed := &observedCardStore{MembershipCardConfigurationStore: repository.NewMembershipCardConfigurations(retryDB), ready: make(chan struct{}), proceed: make(chan struct{})}
		var releaseOnce sync.Once
		release := func() { releaseOnce.Do(func() { close(observed.proceed) }) }
		defer release()
		outcome := make(chan error, 1)
		go func() {
			_, err := NewMembershipCardConfigurations(observed, time.Now).ForLogin(ctx, domain.Account{ID: tc.ID, Role: 1}, tc.Association, audit)
			outcome <- err
		}()
		select {
		case <-observed.ready:
		case <-ctx.Done():
			t.Fatal("configuration did not reach audit commit")
		}
		// Observe the external backend waiting on the association before the
		// config transaction commits and checks its deferred actor FK. This
		// avoids relying on goroutine timing to construct the lock cycle.
		blocked := make(chan error, 1)
		go func() {
			var association string
			blocked <- blocker.GetContext(ctx, &association, `SELECT sport_association_id FROM application_sportassociation WHERE sport_association_id=$1 FOR UPDATE`, tc.Association)
		}()
		ticker := time.NewTicker(5 * time.Millisecond)
		defer ticker.Stop()
		for {
			var waiting bool
			if err := db.GetContext(ctx, &waiting, `SELECT COALESCE(wait_event_type='Lock',false) FROM pg_stat_activity WHERE pid=$1`, blockerPID); err != nil {
				t.Fatal(err)
			}
			if waiting {
				break
			}
			select {
			case err := <-blocked:
				t.Fatalf("external lock did not wait: %v", err)
			case <-ticker.C:
			case <-ctx.Done():
				t.Fatal("external association wait was not observed")
			}
		}
		release()
		select {
		case err := <-blocked:
			if err != nil {
				t.Fatal("external lock cycle failed", err)
			}
		case <-ctx.Done():
			t.Fatal("configuration transaction did not release its deadlocked parent")
		}
		if err = blocker.Commit(); err != nil {
			t.Fatal(err)
		}
		select {
		case err := <-outcome:
			if err != nil {
				t.Fatal("deadlock retry failed", err)
			}
		case <-ctx.Done():
			t.Fatal("deadlock retry did not finish")
		}
		if observed.attempts.Load() < 2 {
			t.Fatal("real transaction conflict was not retried")
		}
	})
	t.Run("foreign owner and missing collaborator owner denied", func(t *testing.T) {
		tc := fixture.Cases[0]
		for _, account := range []domain.Account{{ID: fixture.Cases[1].ID, Role: 1}, {ID: tc.ID, Role: 3}} {
			result, err := service.ForLogin(context.Background(), account, tc.Association, domain.LoginAuditContext{})
			if result != nil || !errors.Is(err, domain.ErrAssociationNotFound) {
				t.Fatal("foreign configuration admitted")
			}
		}
	})
	t.Run("cancelled operation", func(t *testing.T) {
		ctx, cancel := context.WithCancel(context.Background())
		cancel()
		result, err := service.ForLogin(ctx, domain.Account{ID: fixture.Cases[0].ID, Role: 1}, fixture.Cases[0].Association, domain.LoginAuditContext{})
		if result != nil || !errors.Is(err, context.Canceled) {
			t.Fatal("cancellation ignored")
		}
	})
}

func deleteCardFixture(t *testing.T, db *sqlx.DB, association string) {
	t.Helper()
	// Test-only cleanup without Django delete signals, in foreign-key order.
	if _, err := db.Exec(`DELETE FROM application_auditlogindex WHERE log_entry_id IN
  (SELECT id FROM auditlog_logentry WHERE content_type_id=(SELECT id FROM django_content_type WHERE app_label='application' AND model='sportassociationmembershipcardconfiguration')
  AND object_pk IN(SELECT sport_association_membership_card_configuration_id::text FROM application_sportassociationmembershipcardconfiguration WHERE sport_association_id=$1))`, association); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`DELETE FROM auditlog_logentry WHERE content_type_id=(SELECT id FROM django_content_type WHERE app_label='application' AND model='sportassociationmembershipcardconfiguration')
  AND object_pk IN(SELECT sport_association_membership_card_configuration_id::text FROM application_sportassociationmembershipcardconfiguration WHERE sport_association_id=$1)`, association); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Exec(`DELETE FROM application_sportassociationmembershipcardconfiguration WHERE sport_association_id=$1`, association); err != nil {
		t.Fatal(err)
	}
}

type failedCardStore struct {
	domain.MembershipCardConfigurationStore
	mode   string
	cancel context.CancelFunc
}
type failedCardTx struct {
	domain.MembershipCardConfigurationTransaction
	store *failedCardStore
}

func (s *failedCardStore) BeginCardConfiguration(ctx context.Context) (domain.MembershipCardConfigurationTransaction, error) {
	tx, err := s.MembershipCardConfigurationStore.BeginCardConfiguration(ctx)
	if err != nil {
		return nil, err
	}
	return &failedCardTx{tx, s}, nil
}
func (t *failedCardTx) CreateDefault(ctx context.Context, id, association string) error {
	if err := t.MembershipCardConfigurationTransaction.CreateDefault(ctx, id, association); err != nil {
		return err
	}
	if t.store.mode == "after_insert" {
		return errors.New("injected creation failure")
	}
	return nil
}
func (t *failedCardTx) AppendConfigurationAudit(ctx context.Context, event domain.MembershipCardConfigurationAudit) error {
	missing := "00000000-0000-4000-8000-999999999999"
	if t.store.mode == "actor_fk" {
		event.Context.ActorID = &missing
	}
	if t.store.mode == "index_fk" {
		event.AssociationID = missing
	}
	if err := t.MembershipCardConfigurationTransaction.AppendConfigurationAudit(ctx, event); err != nil {
		return err
	}
	if t.store.mode == "after_audit" {
		return errors.New("injected audit failure")
	}
	if t.store.mode == "cancel" {
		t.store.cancel()
	}
	return nil
}
func (t *failedCardTx) Commit() error {
	if t.store.mode == "before_commit" {
		return errors.New("injected commit failure")
	}
	return t.MembershipCardConfigurationTransaction.Commit()
}

type observedCardStore struct {
	domain.MembershipCardConfigurationStore
	attempts atomic.Int64
	ready    chan struct{}
	proceed  chan struct{}
	once     sync.Once
}
type observedCardTx struct {
	domain.MembershipCardConfigurationTransaction
	store *observedCardStore
}

func (s *observedCardStore) BeginCardConfiguration(ctx context.Context) (domain.MembershipCardConfigurationTransaction, error) {
	s.attempts.Add(1)
	tx, err := s.MembershipCardConfigurationStore.BeginCardConfiguration(ctx)
	if err != nil {
		return nil, err
	}
	return &observedCardTx{tx, s}, nil
}
func (t *observedCardTx) Commit() error {
	t.store.once.Do(func() { close(t.store.ready) })
	<-t.store.proceed
	return t.MembershipCardConfigurationTransaction.Commit()
}
