package service

import (
	"context"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	passwordcrypto "github.com/carbogninalberto/assozeta/BE_GO/pkg/crypto"
)

type profilePasswordTx struct {
	id               string
	encoded          string
	writes           []string
	commit, rollback bool
	fail             string
}

func (t *profilePasswordTx) PasswordForUpdate(_ context.Context, id string) (string, error) {
	t.id = id
	if t.fail == "read" {
		return "", errors.New("read")
	}
	return t.encoded, nil
}
func (t *profilePasswordTx) SetPassword(_ context.Context, id, encoded string) error {
	if id != t.id {
		panic("foreign write")
	}
	t.writes = append(t.writes, encoded)
	if t.fail == "write" {
		return errors.New("write")
	}
	return nil
}
func (t *profilePasswordTx) Commit() error {
	t.commit = true
	if t.fail == "commit" {
		return errors.New("commit")
	}
	return nil
}
func (t *profilePasswordTx) Rollback() error { t.rollback = true; return nil }

type profilePasswordStore struct {
	tx     *profilePasswordTx
	begins int
}

func (s *profilePasswordStore) BeginProfilePassword(context.Context) (domain.ProfilePasswordTransaction, error) {
	s.begins++
	return s.tx, nil
}

type profilePasswordHashes struct {
	decision passwordcrypto.LoginPasswordDecision
	calls    int
	encoded  []string
	fail     bool
}

func (h *profilePasswordHashes) CheckDjangoPassword(context.Context, passwordcrypto.LoginPasswordValue, string) (passwordcrypto.LoginPasswordDecision, error) {
	h.calls++
	return h.decision, nil
}
func (h *profilePasswordHashes) Encode(_ context.Context, value string) (string, error) {
	h.encoded = append(h.encoded, value)
	if h.fail {
		return "", errors.New("encode")
	}
	return "encoded:" + value, nil
}

func TestProfilePasswordPolicy(t *testing.T) {
	for _, tc := range []struct {
		value string
		valid bool
	}{
		{"A1!aaaaaaa", true}, {"A1+aaaaaaa", false}, {"À1!aaaaaaa", false}, {"A1!ééééééé", true}, {"A1!aaaaaa", false}, {"A1!aaaaaaa\n", true}, {"A1!aaa\naaaaa", false}, {"A1!aaaaaaa\n\n", false}, {"A1!aaaaaa\r", true},
	} {
		if got := validProfilePassword(tc.value); got != tc.valid {
			t.Errorf("%q got %v", tc.value, got)
		}
	}
}
func TestProfilePasswordMutation(t *testing.T) {
	person := &domain.Account{ID: "collaborator", Role: 3}
	owner := &domain.Account{ID: "owner", Role: 1}
	identity := domain.ResolvedIdentity{Actor: person, Effective: person, ScopeUser: owner, Collaborator: true}
	for _, tc := range []struct {
		name, new      string
		match, upgrade bool
		outcome        ProfilePasswordOutcome
		writes         int
	}{
		{"replace", "A1!aaaaaaa", true, false, ProfilePasswordChanged, 1},
		{"invalid upgrade persists", "weak", true, true, ProfilePasswordInvalid, 1},
		{"wrong old", "A1!aaaaaaa", false, false, ProfilePasswordIncorrect, 0},
		{"invalid wins", "weak", false, false, ProfilePasswordInvalid, 0},
		{"upgrade then replacement", "A1!aaaaaaa", true, true, ProfilePasswordChanged, 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			tx := &profilePasswordTx{encoded: "stored"}
			store := &profilePasswordStore{tx: tx}
			hashes := &profilePasswordHashes{decision: passwordcrypto.LoginPasswordDecision{Match: tc.match, Upgrade: tc.upgrade}}
			s := NewProfilePasswords(store, hashes, 1)
			got, err := s.Change(context.Background(), identity, dto.ProfilePasswordRequest{Current: "old", New: tc.new})
			if err != nil || got != tc.outcome || tx.id != "collaborator" || len(tx.writes) != tc.writes || !tx.commit || !tx.rollback {
				t.Fatalf("outcome=%v err=%v tx=%+v", got, err, tx)
			}
			if len(tx.writes) > 0 {
				wanted := tc.new
				if tc.outcome == ProfilePasswordInvalid {
					wanted = "old"
				}
				if tx.writes[0] != "encoded:"+wanted {
					t.Fatal(tx.writes)
				}
			}
		})
	}
	for _, failure := range []string{"read", "write", "commit"} {
		tx := &profilePasswordTx{encoded: "stored", fail: failure}
		s := NewProfilePasswords(&profilePasswordStore{tx: tx}, &profilePasswordHashes{decision: passwordcrypto.LoginPasswordDecision{Match: true}}, 1)
		if _, err := s.Change(context.Background(), identity, dto.ProfilePasswordRequest{Current: "old", New: "A1!aaaaaaa"}); err == nil || !tx.rollback {
			t.Fatalf("failure %s not rolled back: %v", failure, err)
		}
	}
	for _, bad := range []domain.ResolvedIdentity{{}, {Actor: person, Effective: person, ScopeUser: owner, ImpersonationAssociationID: "association"}} {
		store := &profilePasswordStore{tx: &profilePasswordTx{}}
		s := NewProfilePasswords(store, &profilePasswordHashes{}, 1)
		if _, err := s.Change(context.Background(), bad, dto.ProfilePasswordRequest{Current: "old", New: "A1!aaaaaaa"}); err == nil || store.begins != 0 {
			t.Fatal("invalid identity reached store")
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	store := &profilePasswordStore{tx: &profilePasswordTx{}}
	s := NewProfilePasswords(store, &profilePasswordHashes{}, 1)
	if _, err := s.Change(ctx, identity, dto.ProfilePasswordRequest{}); !errors.Is(err, context.Canceled) || store.begins != 0 {
		t.Fatal(err)
	}
}

func TestProfilePasswordHistoricalRoleLabels(t *testing.T) {
	for _, tc := range []struct {
		role int
		name string
	}{{-2, "association"}, {-1, "athlete"}, {0, "association"}, {2, "athlete"}, {3, "association"}} {
		user := &domain.Account{ID: "person", Role: tc.role}
		flow := NewProfilePasswordResponses(&responseProfiles{profile: &dto.UserAuthProfile{UserID: "person"}}, &responseAssociations{}, &responseImages{})
		result, err := flow.Read(context.Background(), domain.ResolvedIdentity{Actor: user, Effective: user, ScopeUser: user}, ProfilePasswordChanged)
		if err != nil || result.Role != tc.name || result.UserData.SportAssociation != nil {
			t.Fatalf("role %d: %+v %v", tc.role, result, err)
		}
	}
}
