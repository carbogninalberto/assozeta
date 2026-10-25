//go:build credentialconcurrencyintegration

package service

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"os"
	"sync"
	"testing"
	"time"

	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	passwordcrypto "github.com/carbogninalberto/assozeta/BE_GO/pkg/crypto"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
)

type interleavedCredentialStore struct {
	store           domain.PasswordLoginStore
	beforeRead      func(context.Context) error
	afterPage       func(context.Context, []domain.Account) error
	attempts, reads int
}
type interleavedCredentialTx struct {
	domain.PasswordLoginTransaction
	owner *interleavedCredentialStore
}

func (s *interleavedCredentialStore) BeginPasswordLogin(ctx context.Context) (domain.PasswordLoginTransaction, error) {
	s.attempts++
	tx, err := s.store.BeginPasswordLogin(ctx)
	if err != nil {
		return nil, err
	}
	return &interleavedCredentialTx{tx, s}, nil
}
func (tx *interleavedCredentialTx) Candidates(ctx context.Context, email bool, identifier, after string) ([]domain.Account, error) {
	tx.owner.reads++
	if after == "" && tx.owner.beforeRead != nil {
		// Establish the real transaction snapshot without acquiring a candidate row
		// lock, then let another connection perform a normal login finalization.
		if _, err := tx.PasswordLoginTransaction.Candidates(ctx, false, "no-credential-snapshot-candidate", ""); err != nil {
			return nil, err
		}
		if err := tx.owner.beforeRead(ctx); err != nil {
			return nil, err
		}
	}
	rows, err := tx.PasswordLoginTransaction.Candidates(ctx, email, identifier, after)
	if err == nil && tx.owner.afterPage != nil {
		err = tx.owner.afterPage(ctx, rows)
	}
	return rows, err
}

type interleavedCredentialHasher struct {
	original      string
	upgrade       bool
	beforeCheck   func(context.Context) error
	once          sync.Once
	mutationError error
	checks        int
}

func (h *interleavedCredentialHasher) CheckLoginValue(ctx context.Context, _ passwordcrypto.LoginPasswordValue, encoded string) (passwordcrypto.LoginPasswordDecision, error) {
	h.checks++
	h.once.Do(func() {
		if h.beforeCheck != nil {
			h.mutationError = h.beforeCheck(ctx)
		}
	})
	if h.mutationError != nil {
		return passwordcrypto.LoginPasswordDecision{}, h.mutationError
	}
	return passwordcrypto.LoginPasswordDecision{Match: encoded == h.original, Upgrade: h.upgrade && encoded == h.original}, nil
}
func (*interleavedCredentialHasher) Encode(context.Context, string) (string, error) {
	return "!credential-rehash-result", nil
}

func TestCredentialConcurrencyPostgres(t *testing.T) {
	var fixture struct {
		ID, Email, Encoded, BatchEmail string
		BatchIDs                       []string
	}
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_CREDENTIAL_CONCURRENCY_FIXTURE"))
	if err != nil || json.Unmarshal(raw, &fixture) != nil || len(fixture.BatchIDs) != 129 {
		t.Fatal("run accounts.py --only credential-concurrency")
	}
	dbConfig, err := pgx.ParseConfig(os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal(err)
	}
	db := sqlx.NewDb(ownedpgx.OpenDB(*dbConfig), "pgx")
	if err = db.Ping(); err != nil {
		_ = db.Close()
		t.Fatal(err)
	}
	defer db.Close()
	db.SetMaxOpenConns(4)
	writerConfig, err := pgx.ParseConfig(os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal(err)
	}
	writer := sqlx.NewDb(ownedpgx.OpenDB(*writerConfig), "pgx")
	if err = writer.Ping(); err != nil {
		_ = writer.Close()
		t.Fatal(err)
	}
	defer writer.Close()
	writer.SetMaxOpenConns(2)
	clock := func() time.Time { return time.Date(2030, 1, 2, 12, 0, 0, 0, time.UTC) }
	restore := func() {
		if _, err := writer.Exec(`UPDATE bakney_user SET password=$2,last_login=NULL,is_active=TRUE,deleted=FALSE WHERE user_id=$1`, fixture.ID, fixture.Encoded); err != nil {
			t.Error(err)
		}
		if _, err := writer.Exec(`UPDATE bakney_user SET last_login=NULL WHERE email=$1`, fixture.BatchEmail); err != nil {
			t.Error(err)
		}
	}
	defer restore()
	t.Run("unrelated finalization cannot exhaust credential attempts", func(t *testing.T) {
		defer restore()
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		store := &interleavedCredentialStore{store: repository.NewPasswordLogins(db)}
		store.beforeRead = func(ctx context.Context) error {
			return NewLoginFinalization(repository.NewLoginFinalizations(writer), clock).Finalize(ctx, fixture.ID, domain.LoginAuditContext{})
		}
		hasher := &interleavedCredentialHasher{original: fixture.Encoded}
		got, err := NewPasswordLogin(store, hasher, 1).Select(ctx, fixture.Email, "synthetic")
		if err != nil || got.Account == nil || got.Account.ID != fixture.ID || store.attempts != 1 || hasher.checks != 1 {
			t.Fatalf("selection %v, attempts=%d checks=%d identity=%v", err, store.attempts, hasher.checks, got.Account)
		}
	})
	t.Run("concurrent reset is not overwritten by rehash", func(t *testing.T) {
		defer restore()
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		store := &interleavedCredentialStore{store: repository.NewPasswordLogins(db)}
		hasher := &interleavedCredentialHasher{original: fixture.Encoded, upgrade: true, beforeCheck: func(ctx context.Context) error {
			_, err := writer.ExecContext(ctx, `UPDATE bakney_user SET password='!concurrent-reset' WHERE user_id=$1`, fixture.ID)
			return err
		}}
		got, err := NewPasswordLogin(store, hasher, 1).Select(ctx, fixture.Email, "synthetic")
		if err != nil || got.Account != nil || got.Failure != "invalid" || store.attempts != 2 || hasher.checks != 2 {
			t.Fatalf("reset selection %+v/%v attempts=%d checks=%d", got, err, store.attempts, hasher.checks)
		}
		var encoded string
		if err = writer.Get(&encoded, `SELECT password FROM bakney_user WHERE user_id=$1`, fixture.ID); err != nil || encoded != "!concurrent-reset" {
			t.Fatal("reset password overwritten", err)
		}
	})
	for _, deleted := range []bool{false, true} {
		name := "disabled account revoked after snapshot"
		if deleted {
			name = "deleted account revoked after snapshot"
		}
		t.Run(name, func(t *testing.T) {
			defer restore()
			ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			hasher := &interleavedCredentialHasher{original: fixture.Encoded, beforeCheck: func(ctx context.Context) error {
				_, err := writer.ExecContext(ctx, `UPDATE bakney_user SET is_active=$2,deleted=$3 WHERE user_id=$1`, fixture.ID, deleted, deleted)
				return err
			}}
			admission := NewPasswordAdmission(NewPasswordLogin(repository.NewPasswordLogins(db), hasher, 1), NewLoginPreparation(repository.NewLoginPreparations(db), clock), NewSecondFactor(clock))
			got, err := admission.Admit(ctx, fixture.Email, "synthetic", nil, false, domain.LoginAuditContext{})
			want := ErrLoginCredentials
			if deleted {
				want = domain.ErrAccountNotFound
			}
			if got != nil || !errors.Is(err, want) {
				t.Fatalf("revoked account admitted %v/%v", got, err)
			}
		})
	}
	t.Run("later page retains the original candidate snapshot", func(t *testing.T) {
		defer restore()
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		store := &interleavedCredentialStore{store: repository.NewPasswordLogins(db)}
		store.afterPage = func(ctx context.Context, rows []domain.Account) error {
			if len(rows) != 128 {
				return nil
			}
			_, err := writer.ExecContext(ctx, `UPDATE bakney_user SET last_login=$2 WHERE user_id=$1`, fixture.BatchIDs[128], clock())
			return err
		}
		hasher := &interleavedCredentialHasher{original: fixture.Encoded}
		got, err := NewPasswordLogin(store, hasher, 1).Select(ctx, fixture.BatchEmail, "synthetic")
		if err != nil || got.Account != nil || got.Failure != "invalid" || store.attempts != 1 || store.reads != 2 || hasher.checks != 129 {
			t.Fatalf("paging %+v/%v attempts=%d reads=%d checks=%d", got, err, store.attempts, store.reads, hasher.checks)
		}
	})
	t.Run("blocked rehash cancellation releases pool and writes", func(t *testing.T) {
		defer restore()
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		blocker, err := writer.BeginTxx(context.Background(), &sql.TxOptions{})
		if err != nil {
			t.Fatal(err)
		}
		defer blocker.Rollback()
		if _, err = blocker.ExecContext(ctx, `UPDATE bakney_user SET password='!uncommitted-reset' WHERE user_id=$1`, fixture.ID); err != nil {
			t.Fatal(err)
		}
		var blockerPID int
		if err = blocker.GetContext(ctx, &blockerPID, `SELECT pg_backend_pid()`); err != nil {
			t.Fatal(err)
		}
		type outcome struct {
			selection PasswordSelection
			err       error
		}
		done := make(chan outcome, 1)
		go func() {
			got, err := NewPasswordLogin(repository.NewPasswordLogins(db), &interleavedCredentialHasher{original: fixture.Encoded, upgrade: true}, 1).Select(ctx, fixture.Email, "synthetic")
			done <- outcome{got, err}
		}()
		blocked := false
		until := time.Now().Add(5 * time.Second)
		for time.Now().Before(until) && !blocked {
			err = writer.GetContext(ctx, &blocked, `SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE 'UPDATE bakney_user AS u SET password%' AND $1=ANY(pg_blocking_pids(pid)))`, blockerPID)
			if err != nil {
				break
			}
			if !blocked {
				time.Sleep(5 * time.Millisecond)
			}
		}
		cancel()
		result := <-done
		_ = blocker.Rollback()
		if !blocked || err != nil || !errors.Is(result.err, context.Canceled) || result.selection.Account != nil {
			t.Fatalf("rehash wait/cancel %v/%v/%v", blocked, err, result.err)
		}
		var encoded string
		if err = writer.Get(&encoded, `SELECT password FROM bakney_user WHERE user_id=$1`, fixture.ID); err != nil || encoded != fixture.Encoded {
			t.Fatal("canceled rehash changed password", err)
		}
		until = time.Now().Add(time.Second)
		for db.Stats().InUse != 0 && time.Now().Before(until) {
			time.Sleep(time.Millisecond)
		}
		if db.Stats().InUse != 0 {
			t.Fatal("canceled transaction retained pool slot")
		}
		got, err := NewPasswordLogin(repository.NewPasswordLogins(db), &interleavedCredentialHasher{original: fixture.Encoded}, 1).Select(context.Background(), fixture.Email, "synthetic")
		if err != nil || got.Account == nil {
			t.Fatal("pool did not recover", err)
		}
	})
}
