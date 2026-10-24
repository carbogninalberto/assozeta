//go:build loginintegration

package service

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	passwordcrypto "github.com/carbogninalberto/assozeta/BE_GO/pkg/crypto"
	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

func TestPasswordLoginAgainstDjango(t *testing.T) {
	path, dsn := os.Getenv("ASSOZETA_PASSWORD_LOGIN_FIXTURE"), os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN")
	if path == "" || dsn == "" {
		t.Fatal("run TESTS/adapters/django/accounts.py; no skipped credential-stage checks")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Cases []struct {
			Name, Selected, Failure string
			Identifier, Password    json.RawMessage
			IdentifierUpload        *domain.RequestUpload `json:"identifier_upload"`
			PasswordUpload          *domain.RequestUpload `json:"password_upload"`
			Rows                    []struct {
				ID, Encoded string
				Changed     bool
			}
		}
	}
	if json.Unmarshal(raw, &fixture) != nil || len(fixture.Cases) == 0 {
		t.Fatal("invalid synthetic fixture")
	}
	db, err := sqlx.Connect("pgx", dsn)
	if err != nil {
		t.Fatal("private database unavailable")
	}
	defer db.Close()
	db.SetMaxOpenConns(4)
	store := &measuredPasswordStore{store: repository.NewPasswordLogins(db)}
	s := NewPasswordLogin(store, passwordcrypto.NewPasswordVerifier(passwordcrypto.DefaultPasswordLimits()), 2)
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			identifier, parseErr := dto.ParseReferenceJSON(tc.Identifier)
			if parseErr != nil {
				t.Fatal("invalid identifier fixture")
			}
			password, parseErr := dto.ParseReferenceJSON(tc.Password)
			if parseErr != nil {
				t.Fatal("invalid password fixture")
			}
			if tc.IdentifierUpload != nil {
				identifier = *tc.IdentifierUpload
			}
			if tc.PasswordUpload != nil {
				password = *tc.PasswordUpload
			}
			store.reads.Store(0)
			store.writes.Store(0)
			result, err := s.Select(context.Background(), identifier, password)
			if store.reads.Load() != 1 {
				t.Fatal("raw credential lookup exceeded one SELECT for a small candidate set")
			}
			if db.Stats().InUse != 0 {
				t.Fatal("credential transaction leaked a pool connection")
			}
			failure := result.Failure
			if errors.Is(err, passwordcrypto.ErrLoginPasswordValue) {
				failure = "value"
				err = nil
			}
			if err != nil {
				t.Fatalf("credential stage failed: %v", err)
			}
			id := ""
			if result.Account != nil {
				id = result.Account.ID
				if result.Account.Password != "" {
					t.Fatal("hash leaked from credential stage")
				}
			}
			if id != tc.Selected || failure != tc.Failure {
				t.Fatal("selected account/denial differs from explicit contract")
			}
			for _, row := range tc.Rows {
				var encoded string
				if db.Get(&encoded, `SELECT password FROM bakney_user WHERE user_id=$1`, row.ID) != nil {
					t.Fatal("account missing")
				}
				if (encoded != row.Encoded) != row.Changed {
					t.Fatal("password write set differs")
				}
			}
		})
	}
}

// Query/admission tests substitute the expensive KDF only. PostgreSQL transactions,
// locks, paging, updates and rollback remain real; hash compatibility is above.
type measuredPasswordStore struct {
	store         domain.PasswordLoginStore
	reads, writes atomic.Int64
	failCommit    bool
}
type measuredPasswordTx struct {
	domain.PasswordLoginTransaction
	owner *measuredPasswordStore
}

func (s *measuredPasswordStore) BeginPasswordLogin(ctx context.Context) (domain.PasswordLoginTransaction, error) {
	tx, err := s.store.BeginPasswordLogin(ctx)
	if err != nil {
		return nil, err
	}
	return &measuredPasswordTx{tx, s}, nil
}
func (t *measuredPasswordTx) Candidates(ctx context.Context, email bool, identifier, after string) ([]domain.Account, error) {
	t.owner.reads.Add(1)
	return t.PasswordLoginTransaction.Candidates(ctx, email, identifier, after)
}
func (t *measuredPasswordTx) ReplacePasswords(ctx context.Context, rows []domain.PasswordReplacement) error {
	if len(rows) > 0 {
		t.owner.writes.Add(1)
	}
	return t.PasswordLoginTransaction.ReplacePasswords(ctx, rows)
}
func (t *measuredPasswordTx) Commit() error {
	if t.owner.failCommit {
		return errors.New("injected precommit failure")
	}
	return t.PasswordLoginTransaction.Commit()
}

type measuredLoginHasher struct {
	active, maximum atomic.Int64
	upgrade         bool
	encoded         string
}

func (h *measuredLoginHasher) CheckLoginValue(ctx context.Context, _ passwordcrypto.LoginPasswordValue, encoded string) (passwordcrypto.LoginPasswordDecision, error) {
	active := h.active.Add(1)
	defer h.active.Add(-1)
	for {
		old := h.maximum.Load()
		if old >= active || h.maximum.CompareAndSwap(old, active) {
			break
		}
	}
	select {
	case <-time.After(time.Millisecond):
	case <-ctx.Done():
		return passwordcrypto.LoginPasswordDecision{}, ctx.Err()
	}
	return passwordcrypto.LoginPasswordDecision{Match: true, Upgrade: h.upgrade && encoded == "!query-count-only"}, nil
}
func (h *measuredLoginHasher) Encode(context.Context, string) (string, error) { return h.encoded, nil }
func TestPasswordLoginTransactionsAndBounds(t *testing.T) {
	data, err := os.ReadFile(os.Getenv("ASSOZETA_PASSWORD_LOGIN_FIXTURE"))
	if err != nil {
		t.Fatal("fixture required")
	}
	var fixture struct {
		Batches []struct {
			Identifier string
			IDs        []string
		}
	}
	if json.Unmarshal(data, &fixture) != nil || len(fixture.Batches) != 5 {
		t.Fatal("five query-size fixtures required")
	}
	db, err := sqlx.Connect("pgx", os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal("database unavailable")
	}
	defer db.Close()
	db.SetMaxOpenConns(4)
	for _, batch := range fixture.Batches {
		store := &measuredPasswordStore{store: repository.NewPasswordLogins(db)}
		h := &measuredLoginHasher{}
		s := NewPasswordLogin(store, h, 2)
		result, err := s.Select(context.Background(), batch.Identifier, "synthetic")
		if err != nil {
			t.Fatal(err)
		}
		if len(batch.IDs) == 1 {
			if result.Account == nil || result.Account.ID != batch.IDs[0] {
				t.Fatal("singleton not selected")
			}
		} else if result.Failure != "invalid" || result.Account != nil {
			t.Fatal("ambiguous page set admitted")
		}
		expected := int64(len(batch.IDs)/domain.PasswordCandidatePage + 1)
		if store.reads.Load() != expected || store.writes.Load() != 0 {
			t.Fatal("queries grew per candidate or read-only credentials were rewritten")
		}

		store.reads.Store(0)
		h.upgrade, h.encoded = true, "test-only-replacement"
		if _, err := s.Select(context.Background(), batch.Identifier, "synthetic"); err != nil {
			t.Fatal(err)
		}
		if store.reads.Load() != expected || store.writes.Load() != int64((len(batch.IDs)+127)/128) {
			t.Fatal("password writes grew per candidate")
		}
		var count int
		if db.Get(&count, `SELECT count(*) FROM bakney_user WHERE email=$1 AND password=$2`, batch.Identifier, "test-only-replacement") != nil || count != len(batch.IDs) {
			t.Fatal("page upgrade omitted candidates")
		}
		if _, err := db.Exec(`UPDATE bakney_user SET password=$1 WHERE email=$2`, "!query-count-only", batch.Identifier); err != nil {
			t.Fatal(err)
		}
	}
	batch := fixture.Batches[0]
	for _, failure := range []string{"commit", "constraint"} {
		store := &measuredPasswordStore{store: repository.NewPasswordLogins(db), failCommit: failure == "commit"}
		encoded := "test-only-replacement"
		if failure == "constraint" {
			encoded = strings.Repeat("x", 200)
		}
		result, err := NewPasswordLogin(store, &measuredLoginHasher{upgrade: true, encoded: encoded}, 1).Select(context.Background(), batch.Identifier, "synthetic")
		if err == nil || result.Account != nil {
			t.Fatal("failed transaction admitted identity")
		}
		var password string
		if db.Get(&password, `SELECT password FROM bakney_user WHERE user_id=$1`, batch.IDs[0]) != nil || password != "!query-count-only" {
			t.Fatal("failed transaction retained password write")
		}
	}
	h := &measuredLoginHasher{upgrade: true, encoded: "test-only-replacement"}
	s := NewPasswordLogin(repository.NewPasswordLogins(db), h, 2)
	var wg sync.WaitGroup
	for i := 0; i < 16; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			result, err := s.Select(context.Background(), batch.Identifier, "synthetic")
			if err != nil || result.Account == nil {
				t.Error("concurrent credential admission failed")
			}
		}()
	}
	wg.Wait()
	var final string
	if db.Get(&final, `SELECT password FROM bakney_user WHERE user_id=$1`, batch.IDs[0]) != nil || final != "test-only-replacement" {
		t.Fatal("concurrent upgrade missing")
	}
	if _, err := db.Exec(`UPDATE bakney_user SET password=$1 WHERE user_id=$2`, "!query-count-only", batch.IDs[0]); err != nil {
		t.Fatal(err)
	}
	if h.maximum.Load() > 2 || db.Stats().InUse != 0 {
		t.Fatal("hash concurrency or database pool leaked")
	}
}
