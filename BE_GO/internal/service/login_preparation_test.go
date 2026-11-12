package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type preparationStub struct {
	state                 domain.LoginChallenge
	associations          map[string]string
	events                []string
	audit                 domain.UsernameAudit
	failAudit, failCommit bool
	committed, rolled     bool
}

func (s *preparationStub) BeginLoginPreparation(context.Context) (domain.LoginPreparationTransaction, error) {
	return s, nil
}
func (s *preparationStub) AccountForUpdate(context.Context, string) (domain.LoginChallenge, error) {
	return s.state, nil
}
func (s *preparationStub) SetUsername(_ context.Context, _, value string) error {
	s.events = append(s.events, "username")
	return nil
}
func (s *preparationStub) AuditAssociations(context.Context, string, *string) (map[string]string, error) {
	return s.associations, nil
}
func (s *preparationStub) AppendUsernameAudit(_ context.Context, value domain.UsernameAudit) error {
	s.events = append(s.events, "audit")
	s.audit = value
	if s.failAudit {
		return errors.New("audit unavailable")
	}
	return nil
}
func (s *preparationStub) Commit() error {
	if s.failCommit {
		return errors.New("commit unavailable")
	}
	s.committed = true
	return nil
}
func (s *preparationStub) Rollback() error { s.rolled = true; return nil }
func TestLoginPreparationNormalizesAndAudits(t *testing.T) {
	for _, tc := range []struct{ input, want string }{{"straße", "STRASSE"}, {"ﬃ-user", "FFI-USER"}, {"ıσςǰ", "IΣΣJ̌"}, {"ALREADY", "ALREADY"}} {
		s := &preparationStub{state: domain.LoginChallenge{Account: domain.Account{ID: "target", Username: tc.input, Active: true}, FirstName: "First", LastName: "Last"}, associations: map[string]string{"target": "tenant"}}
		gate := NewLoginPreparation(s, func() time.Time { return time.Unix(123, 0) })
		result, err := gate.Prepare(context.Background(), "target", domain.LoginAuditContext{})
		if err != nil || result.Username != tc.want || !s.committed {
			t.Fatal("normalization failed")
		}
		if tc.input != tc.want {
			if len(s.events) != 2 || s.audit.Before != tc.input || s.audit.After != tc.want || s.audit.ObjectRepresentation != "name: First Last" || s.audit.AssociationID == nil || *s.audit.ResolutionPath != "user.sport_association" {
				t.Fatal("username audit missing or incorrectly scoped")
			}
		} else if len(s.events) != 0 {
			t.Fatal("unchanged username generated writes")
		}
	}
}
func TestLoginPreparationFailureReturnsNoChallenge(t *testing.T) {
	for _, failure := range []string{"inactive", "audit", "commit"} {
		s := &preparationStub{state: domain.LoginChallenge{Account: domain.Account{ID: "target", Username: "mixed", Active: failure != "inactive"}}, failAudit: failure == "audit", failCommit: failure == "commit"}
		result, err := NewLoginPreparation(s, time.Now).Prepare(context.Background(), "target", domain.LoginAuditContext{})
		if err == nil || result.ID != "" || s.committed || !s.rolled {
			t.Fatal("failed preparation returned a challenge or retained transaction")
		}
	}
}

type conflictPreparationStore struct{ attempts int }
type conflictPreparationTx struct {
	preparationStub
	conflict bool
}

func (s *conflictPreparationStore) BeginLoginPreparation(context.Context) (domain.LoginPreparationTransaction, error) {
	s.attempts++
	return &conflictPreparationTx{preparationStub: preparationStub{state: domain.LoginChallenge{Account: domain.Account{ID: "target", Username: "mixed", Active: true}}}, conflict: s.attempts == 1}, nil
}
func (t *conflictPreparationTx) Commit() error {
	if t.conflict {
		return domain.ErrCredentialConflict
	}
	return t.preparationStub.Commit()
}
func TestLoginPreparationRetriesTransactionConflict(t *testing.T) {
	store := &conflictPreparationStore{}
	result, err := NewLoginPreparation(store, time.Now).Prepare(context.Background(), "target", domain.LoginAuditContext{})
	if err != nil || result.Username != "MIXED" || store.attempts != 2 {
		t.Fatal("normalization conflict was not retried")
	}
}
