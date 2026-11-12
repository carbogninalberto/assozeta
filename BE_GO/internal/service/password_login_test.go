package service

import (
	"context"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	passwordcrypto "github.com/carbogninalberto/assozeta/BE_GO/pkg/crypto"
)

type loginTransactionStub struct {
	rows              []domain.Account
	writes            []domain.PasswordReplacement
	committed, rolled bool
	fail              bool
}

func (s *loginTransactionStub) Candidates(context.Context, bool, string, string) ([]domain.Account, error) {
	rows := s.rows
	s.rows = nil
	return rows, nil
}
func (s *loginTransactionStub) ReplacePasswords(_ context.Context, rows []domain.PasswordReplacement) error {
	if s.fail {
		return errors.New("write failed")
	}
	s.writes = append(s.writes, rows...)
	return nil
}
func (s *loginTransactionStub) Commit() error   { s.committed = true; return nil }
func (s *loginTransactionStub) Rollback() error { s.rolled = true; return nil }
func (s *loginTransactionStub) BeginPasswordLogin(context.Context) (domain.PasswordLoginTransaction, error) {
	return s, nil
}

type loginHasherStub struct{}

func (loginHasherStub) CheckLoginValue(_ context.Context, password passwordcrypto.LoginPasswordValue, encoded string) (passwordcrypto.LoginPasswordDecision, error) {
	return passwordcrypto.LoginPasswordDecision{Match: encoded != "wrong", Upgrade: encoded != "current" && encoded != "wrong", Legacy: encoded == "legacy"}, nil
}
func (loginHasherStub) Encode(context.Context, string) (string, error) { return "new-hash", nil }
func TestPasswordSelectionAndUpgradeOrder(t *testing.T) {
	for _, tc := range []struct {
		name     string
		rows     []domain.Account
		selected string
		failure  string
		writes   int
	}{
		{"missing", nil, "", "not_found", 0},
		{"wrong", []domain.Account{{ID: "1", Username: "name", Password: "wrong", Active: true}}, "", "invalid", 0},
		{"ambiguous django upgrades", []domain.Account{{ID: "1", Username: "first", Password: "old", Active: true}, {ID: "2", Username: "second", Password: "old", Active: true}}, "", "invalid", 2},
		{"ambiguous legacy untouched", []domain.Account{{ID: "1", Password: "legacy", Active: true}, {ID: "2", Password: "legacy", Active: true}}, "", "invalid", 0},
		{"preferred wins", []domain.Account{{ID: "1", Username: "NAME", Password: "legacy", Active: true}, {ID: "2", Username: "second", Password: "old", Active: true}}, "1", "", 2},
		{"disabled legacy migrates", []domain.Account{{ID: "1", Username: "name", Password: "legacy"}}, "", "invalid", 1},
		{"unicode fold", []domain.Account{{ID: "1", Username: "naMe", Password: "current", Active: true}}, "1", "", 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			tx := &loginTransactionStub{rows: tc.rows}
			s := NewPasswordLogin(tx, loginHasherStub{}, 1)
			result, err := s.Select(context.Background(), " name ", "password")
			if err != nil || result.Failure != tc.failure || len(tx.writes) != tc.writes || !tx.committed {
				t.Fatal("credential stage outcome or writes differ")
			}
			id := ""
			if result.Account != nil {
				id = result.Account.ID
				if result.Account.Password != "" {
					t.Fatal("selected account leaked hash")
				}
			}
			if id != tc.selected {
				t.Fatal("wrong account selected")
			}
		})
	}
}
func TestPasswordWriteFailureCannotAdmit(t *testing.T) {
	tx := &loginTransactionStub{rows: []domain.Account{{ID: "1", Username: "name", Password: "old", Active: true}}, fail: true}
	s := NewPasswordLogin(tx, loginHasherStub{}, 1)
	result, err := s.Select(context.Background(), "name", "password")
	if err == nil || result.Account != nil || tx.committed || !tx.rolled {
		t.Fatal("failed credential write admitted account or left transaction open")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	result, err = s.Select(ctx, "name", "password")
	if !errors.Is(err, context.Canceled) || result.Account != nil {
		t.Fatal("cancelled login admitted")
	}
}

type conflictPasswordStore struct{ attempts int }
type conflictPasswordTx struct {
	loginTransactionStub
	fail bool
}

func (s *conflictPasswordStore) BeginPasswordLogin(context.Context) (domain.PasswordLoginTransaction, error) {
	s.attempts++
	return &conflictPasswordTx{loginTransactionStub: loginTransactionStub{rows: []domain.Account{{ID: "1", Username: "name", Active: true, Password: "current"}}}, fail: s.attempts == 1}, nil
}
func (t *conflictPasswordTx) Commit() error {
	if t.fail {
		return domain.ErrCredentialConflict
	}
	return t.loginTransactionStub.Commit()
}
func TestPasswordLoginRetriesSerializationConflict(t *testing.T) {
	store := &conflictPasswordStore{}
	result, err := NewPasswordLogin(store, loginHasherStub{}, 1).Select(context.Background(), "name", "password")
	if err != nil || result.Account == nil || store.attempts != 2 {
		t.Fatal("concurrent credential update was not retried")
	}
}

func TestPasswordLoginWaitingAdmissionCanCancel(t *testing.T) {
	tx := &loginTransactionStub{}
	s := NewPasswordLogin(tx, loginHasherStub{}, 1)
	s.slots <- struct{}{}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		result, err := s.Select(ctx, "name", "password")
		if result.Account != nil {
			done <- errors.New("cancelled waiter admitted")
			return
		}
		done <- err
	}()
	cancel()
	if err := <-done; !errors.Is(err, context.Canceled) {
		t.Fatal("waiting admission ignored cancellation")
	}
	<-s.slots
	if tx.committed || tx.rolled {
		t.Fatal("cancelled waiter opened a transaction")
	}
}
