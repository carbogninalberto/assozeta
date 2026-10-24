//go:build admissionintegration

package service

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/jackc/pgx/v5"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	passwordcrypto "github.com/carbogninalberto/assozeta/BE_GO/pkg/crypto"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

func TestPasswordAdmissionAgainstDjango(t *testing.T) {
	path := os.Getenv("ASSOZETA_LOGIN_PREPARATION_FIXTURE")
	if path == "" {
		t.Fatal("run TESTS/adapters/django/accounts.py")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Cases []struct {
			Name, ID, Identifier, Password, After, Outcome string
			OTP                                            json.RawMessage
			Present                                        bool
			Audit                                          domain.LoginAuditContext
		}
	}
	if json.Unmarshal(data, &fixture) != nil || len(fixture.Cases) == 0 {
		t.Fatal("invalid admission fixture")
	}
	db, err := sqlx.Connect("pgx", os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal("private database unavailable")
	}
	defer db.Close()
	db.SetMaxOpenConns(4)
	flow := NewPasswordAdmission(NewPasswordLogin(repository.NewPasswordLogins(db), passwordcrypto.NewPasswordVerifier(passwordcrypto.DefaultPasswordLimits()), 2), NewLoginPreparation(repository.NewLoginPreparations(db), time.Now), NewSecondFactor(func() time.Time { return time.Unix(59, 0) }))
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			otp, err := dto.ParseReferenceJSON(tc.OTP)
			if err != nil {
				t.Fatal("invalid fixture OTP")
			}
			account, err := flow.Admit(context.Background(), tc.Identifier, tc.Password, otp, tc.Present, tc.Audit)
			outcome := ""
			switch {
			case err == nil:
			case errors.Is(err, ErrOTPRequired):
				outcome = "required"
			case errors.Is(err, ErrOTPInvalid):
				outcome = "invalid_otp"
			case errors.Is(err, ErrLoginCredentials):
				outcome = "invalid_credentials"
			case errors.Is(err, ErrLoginNotFound):
				outcome = "not_found"
			default:
				outcome = "server_error"
			}
			if outcome != tc.Outcome {
				t.Fatalf("admission = %s; want %s; error: %v", outcome, tc.Outcome, err)
			}
			if outcome == "" {
				if account == nil || account.ID != tc.ID || account.Username != tc.After || account.Password != "" {
					t.Fatal("wrong admitted account or secret leaked")
				}
			} else if account != nil {
				t.Fatal("failed admission returned account")
			}
		})
	}
}

type auditQueryCounter struct{ count atomic.Int64 }

func (q *auditQueryCounter) TraceQueryStart(ctx context.Context, _ *pgx.Conn, data pgx.TraceQueryStartData) context.Context {
	sql := strings.ToLower(strings.TrimSpace(data.SQL))
	if strings.HasPrefix(sql, "select") || strings.HasPrefix(sql, "insert") || strings.HasPrefix(sql, "update") {
		q.count.Add(1)
	}
	return ctx
}
func (q *auditQueryCounter) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

type failedPreparationStore struct {
	domain.LoginPreparationStore
	mode   string
	cancel context.CancelFunc
}
type failedPreparationTx struct {
	domain.LoginPreparationTransaction
	owner *failedPreparationStore
}

func (s *failedPreparationStore) BeginLoginPreparation(ctx context.Context) (domain.LoginPreparationTransaction, error) {
	tx, err := s.LoginPreparationStore.BeginLoginPreparation(ctx)
	if err != nil {
		return nil, err
	}
	return &failedPreparationTx{tx, s}, nil
}
func (t *failedPreparationTx) AppendUsernameAudit(ctx context.Context, event domain.UsernameAudit) error {
	if t.owner.mode == "index" {
		missing := "00000000-0000-4000-8000-999999999999"
		event.AssociationID = &missing
	}
	err := t.LoginPreparationTransaction.AppendUsernameAudit(ctx, event)
	if err == nil && t.owner.mode == "cancel" {
		t.owner.cancel()
	}
	return err
}
func (t *failedPreparationTx) Commit() error {
	if t.owner.mode == "commit" {
		return errors.New("injected precommit failure")
	}
	return t.LoginPreparationTransaction.Commit()
}

func TestLoginPreparationPostgresAtomicityAndConcurrency(t *testing.T) {
	dsn := os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN")
	if dsn == "" {
		t.Fatal("private PostgreSQL fixture required")
	}
	config, err := pgx.ParseConfig(dsn)
	if err != nil {
		t.Fatal("invalid private database configuration")
	}
	counter := &auditQueryCounter{}
	config.Tracer = counter
	db := sqlx.NewDb(stdlib.OpenDB(*config), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	store := repository.NewLoginPreparations(db)
	id := "00000000-0000-4000-8000-000000052000"
	verifyUnchanged := func(id, username string) {
		t.Helper()
		var value string
		var count int
		if db.Get(&value, `SELECT username FROM bakney_user WHERE user_id=$1`, id) != nil || value != username {
			t.Fatal("failed invariant retained username mutation")
		}
		if db.Get(&count, `SELECT count(*) FROM auditlog_logentry WHERE object_pk=$1`, id) != nil || count != 0 {
			t.Fatal("failed invariant retained audit event")
		}
	}
	verifyUnchanged(id, "Audit-Atomic")
	for _, mode := range []string{"index", "commit", "cancel", "actor"} {
		ctx, cancel := context.WithCancel(context.Background())
		injected := &failedPreparationStore{LoginPreparationStore: store, mode: mode, cancel: cancel}
		audit := domain.LoginAuditContext{}
		if mode == "actor" {
			missing := "00000000-0000-4000-8000-999999999999"
			audit.ActorID = &missing
		}
		result, err := NewLoginPreparation(injected, time.Now).Prepare(ctx, id, audit)
		cancel()
		if err == nil || result.ID != "" {
			t.Fatal("failed audit invariant admitted challenge")
		}
		verifyUnchanged(id, "Audit-Atomic")
	}
	collision := "00000000-0000-4000-8000-000000052002"
	if result, err := NewLoginPreparation(store, time.Now).Prepare(context.Background(), collision, domain.LoginAuditContext{}); err == nil || result.ID != "" {
		t.Fatal("username collision admitted")
	}
	verifyUnchanged(collision, "Audit-Collision")
	counter.count.Store(0)
	gate := NewLoginPreparation(store, time.Now)
	result, err := gate.Prepare(context.Background(), id, domain.LoginAuditContext{})
	if err != nil || result.Username != "AUDIT-ATOMIC" || counter.count.Load() != 6 {
		t.Fatalf("changed username query count=%d error=%v", counter.count.Load(), err)
	}
	counter.count.Store(0)
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			result, err := gate.Prepare(context.Background(), id, domain.LoginAuditContext{})
			if err != nil || result.Username != "AUDIT-ATOMIC" {
				t.Error("concurrent preparation failed")
			}
		}()
	}
	wg.Wait()
	if counter.count.Load() != 64 || db.Stats().InUse != 0 {
		t.Fatal("unchanged preparation did extra reads/writes or leaked pool connections")
	}
	var count int
	if db.Get(&count, `SELECT count(*) FROM auditlog_logentry WHERE object_pk=$1`, id) != nil || count != 1 {
		t.Fatal("normalization generated duplicate audits")
	}
	if db.Get(&count, `SELECT count(*) FROM application_auditlogindex i JOIN auditlog_logentry l ON l.id=i.log_entry_id WHERE l.object_pk=$1`, id) != nil || count != 1 {
		t.Fatal("normalization index missing or duplicated")
	}
	// Restore only the private fixture; application invariants above use service transactions.
	if _, err = db.Exec(`DELETE FROM application_auditlogindex WHERE log_entry_id IN (SELECT id FROM auditlog_logentry WHERE object_pk=$1)`, id); err != nil {
		t.Fatal(err)
	}
	if _, err = db.Exec(`DELETE FROM auditlog_logentry WHERE object_pk=$1`, id); err != nil {
		t.Fatal(err)
	}
	if _, err = db.Exec(`UPDATE bakney_user SET username=$1 WHERE user_id=$2`, "Audit-Atomic", id); err != nil {
		t.Fatal(err)
	}
	counter.count.Store(0)
	wg = sync.WaitGroup{}
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			result, err := gate.Prepare(context.Background(), id, domain.LoginAuditContext{})
			if err != nil || result.ID != id {
				t.Error("concurrent normalization failed")
			}
		}()
	}
	wg.Wait()
	if counter.count.Load() != 68 {
		t.Fatalf("concurrent normalization queries=%d; want 68 including 32 server fences", counter.count.Load())
	}
	if db.Get(&count, `SELECT count(*) FROM auditlog_logentry WHERE object_pk=$1`, id) != nil || count != 1 {
		t.Fatal("concurrent normalization created duplicate audits")
	}
	if _, err = db.Exec(`DELETE FROM application_auditlogindex WHERE log_entry_id IN (SELECT id FROM auditlog_logentry WHERE object_pk=$1)`, id); err != nil {
		t.Fatal(err)
	}
	if _, err = db.Exec(`DELETE FROM auditlog_logentry WHERE object_pk=$1`, id); err != nil {
		t.Fatal(err)
	}
	if _, err = db.Exec(`UPDATE bakney_user SET username=$1 WHERE user_id=$2`, "Audit-Atomic", id); err != nil {
		t.Fatal(err)
	}
}

type crossActorPreparationStore struct {
	domain.LoginPreparationStore
	arrivals atomic.Int64
	ready    chan struct{}
}
type crossActorPreparationTx struct {
	domain.LoginPreparationTransaction
	owner *crossActorPreparationStore
}

func (s *crossActorPreparationStore) BeginLoginPreparation(ctx context.Context) (domain.LoginPreparationTransaction, error) {
	tx, err := s.LoginPreparationStore.BeginLoginPreparation(ctx)
	if err != nil {
		return nil, err
	}
	return &crossActorPreparationTx{tx, s}, nil
}
func (t *crossActorPreparationTx) SetUsername(ctx context.Context, id, value string) error {
	if err := t.LoginPreparationTransaction.SetUsername(ctx, id, value); err != nil {
		return err
	}
	switch t.owner.arrivals.Add(1) {
	case 1:
		select {
		case <-t.owner.ready:
		case <-ctx.Done():
			return ctx.Err()
		}
	case 2:
		close(t.owner.ready)
	}
	return nil
}
func TestLoginPreparationPostgresDeadlockRecovery(t *testing.T) {
	db, err := sqlx.Connect("pgx", os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal("private database unavailable")
	}
	defer db.Close()
	db.SetMaxOpenConns(4)
	store := &crossActorPreparationStore{LoginPreparationStore: repository.NewLoginPreparations(db), ready: make(chan struct{})}
	gate := NewLoginPreparation(store, time.Now)
	ids := []string{"00000000-0000-4000-8000-000000052000", "00000000-0000-4000-8000-000000052003"}
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	var wg sync.WaitGroup
	for i, id := range ids {
		wg.Add(1)
		go func(i int, id string) {
			defer wg.Done()
			actor := ids[1-i]
			state, err := gate.Prepare(ctx, id, domain.LoginAuditContext{ActorID: &actor})
			if err != nil || state.ID != id {
				t.Errorf("cross-actor normalization failed: %v", err)
			}
		}(i, id)
	}
	wg.Wait()
	if store.arrivals.Load() < 3 {
		t.Fatal("expected PostgreSQL deadlock did not exercise a retry")
	}
	for i, id := range ids {
		var count int
		if db.Get(&count, `SELECT count(*) FROM auditlog_logentry WHERE object_pk=$1 AND actor_id=$2`, id, ids[1-i]) != nil || count != 1 {
			t.Fatal("deadlock retry lost or duplicated audit")
		}
		if _, err = db.Exec(`DELETE FROM application_auditlogindex WHERE log_entry_id IN (SELECT id FROM auditlog_logentry WHERE object_pk=$1)`, id); err != nil {
			t.Fatal(err)
		}
		if _, err = db.Exec(`DELETE FROM auditlog_logentry WHERE object_pk=$1`, id); err != nil {
			t.Fatal(err)
		}
		username := "Audit-Atomic"
		if i == 1 {
			username = "Audit-Atomic-Peer"
		}
		if _, err = db.Exec(`UPDATE bakney_user SET username=$1 WHERE user_id=$2`, username, id); err != nil {
			t.Fatal(err)
		}
	}
}
