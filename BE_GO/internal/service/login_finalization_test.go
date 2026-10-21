package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type finalizationStub struct {
	state               domain.LoginFinalizationState
	associations        map[string]string
	audit               *domain.LoginDeletionAudit
	at                  time.Time
	clear, committed    bool
	attempts, rollbacks int
	fail                string
}

func (s *finalizationStub) BeginLoginFinalization(context.Context) (domain.LoginFinalizationTransaction, error) {
	s.attempts++
	return s, nil
}
func (s *finalizationStub) FinalizationForUpdate(context.Context, string) (domain.LoginFinalizationState, error) {
	return s.state, nil
}
func (s *finalizationStub) SetLoginTime(_ context.Context, _ string, at time.Time, clear bool) error {
	s.at = at
	s.clear = clear
	return nil
}
func (s *finalizationStub) AuditAssociations(context.Context, string, *string) (map[string]string, error) {
	return s.associations, nil
}
func (s *finalizationStub) AppendLoginDeletionAudit(_ context.Context, a domain.LoginDeletionAudit) error {
	s.audit = &a
	if s.fail == "audit" {
		return errors.New("audit unavailable")
	}
	return nil
}
func (s *finalizationStub) Commit() error {
	if s.fail == "conflict" || (s.fail == "retry" && s.attempts == 1) {
		return domain.ErrCredentialConflict
	}
	if s.fail == "commit" {
		return errors.New("commit unavailable")
	}
	s.committed = true
	return nil
}
func (s *finalizationStub) Rollback() error { s.rollbacks++; return nil }
func TestLoginFinalizationDatesAndAudit(t *testing.T) {
	for _, tc := range []struct {
		name, date, first, second string
		clear                     bool
	}{
		{"null", "", "2026-10-21T12:00:00Z", "", false},
		{"past", "2026-10-20", "2026-10-21T12:00:00Z", "2026-10-21T12:00:00Z", false},
		{"today", "2026-10-21", "2026-10-21T12:00:00Z", "2026-10-21T12:00:00Z", true},
		{"future", "2026-10-22", "2026-10-21T12:00:00Z", "2026-10-21T12:00:00Z", true},
		{"UTC date despite Rome midnight", "2026-10-21", "2026-10-22T00:30:00+02:00", "2026-10-22T00:30:00+02:00", true},
		{"midnight between clock reads", "2026-10-21", "2026-10-21T23:59:59.999999Z", "2026-10-22T00:00:00Z", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := &finalizationStub{state: domain.LoginFinalizationState{ID: "target", FirstName: "First", LastName: "Last"}, associations: map[string]string{"target": "own", "actor": "actor-own"}}
			if tc.date != "" {
				d, _ := time.Parse(time.DateOnly, tc.date)
				s.state.DeleteOn = &d
			}
			first, _ := time.Parse(time.RFC3339Nano, tc.first)
			second, _ := time.Parse(time.RFC3339Nano, tc.second)
			calls := 0
			actor, email := "actor", "actor@example.test"
			err := NewLoginFinalization(s, func() time.Time {
				calls++
				if calls == 1 {
					return first
				}
				return second
			}).Finalize(context.Background(), "target", domain.LoginAuditContext{ActorID: &actor, ActorEmail: &email})
			wantCalls := 1
			if tc.date != "" {
				wantCalls = 2
			}
			if err != nil || !s.committed || !s.at.Equal(first) || s.clear != tc.clear || calls != wantCalls {
				t.Fatalf("wrong finalization: %v calls=%d clear=%v", err, calls, s.clear)
			}
			if tc.clear {
				if s.audit == nil || s.audit.Before != tc.date || s.audit.ObjectRepresentation != "name: First Last" || s.audit.AssociationID == nil || *s.audit.AssociationID != "own" || *s.audit.ResolutionPath != "user.sport_association" {
					t.Fatal("incorrect cancellation audit")
				}
			} else if s.audit != nil {
				t.Fatal("last_login must not audit")
			}
		})
	}
}
func TestLoginFinalizationFailuresAndRetry(t *testing.T) {
	for _, failure := range []string{"audit", "commit", "conflict", "retry", "foreign", "cancel"} {
		t.Run(failure, func(t *testing.T) {
			d := time.Date(2026, 10, 21, 0, 0, 0, 0, time.UTC)
			s := &finalizationStub{state: domain.LoginFinalizationState{ID: "target", DeleteOn: &d}, fail: failure}
			if failure == "foreign" {
				s.state.ID = "foreign"
			}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if failure == "cancel" {
				cancel()
			}
			err := NewLoginFinalization(s, func() time.Time { return d }).Finalize(ctx, "target", domain.LoginAuditContext{})
			if failure == "retry" {
				if err != nil || !s.committed || s.attempts != 2 {
					t.Fatal("conflict not retried")
				}
				return
			}
			if err == nil || s.committed {
				t.Fatal("failed finalization succeeded")
			}
			if s.rollbacks != s.attempts {
				t.Fatal("transaction not released")
			}
			if failure == "conflict" && s.attempts != 3 {
				t.Fatal("retry not bounded")
			}
		})
	}
}
func TestLoginFinalizationActorFallback(t *testing.T) {
	for _, hasActor := range []bool{false, true} {
		d := time.Now().UTC()
		actor, email := "actor", "actor@example.test"
		s := &finalizationStub{state: domain.LoginFinalizationState{ID: "target", DeleteOn: &d}, associations: map[string]string{"actor": "actor-own"}}
		audit := domain.LoginAuditContext{ActorEmail: &email}
		if hasActor {
			audit.ActorID = &actor
		}
		if err := NewLoginFinalization(s, func() time.Time { return d }).Finalize(context.Background(), "target", audit); err != nil {
			t.Fatal(err)
		}
		if hasActor {
			if s.audit.AssociationID == nil || *s.audit.ResolutionPath != "actor.sport_association" {
				t.Fatal("missing actor fallback")
			}
		} else if s.audit.Context.ActorEmail != nil || s.audit.AssociationID != nil {
			t.Fatal("anonymous audit inherited actor")
		}
	}
}
