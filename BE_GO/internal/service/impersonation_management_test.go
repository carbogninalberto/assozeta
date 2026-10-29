package service

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestImpersonationLocalPythonUUIDConstructors(t *testing.T) {
	hex := strings.ReplaceAll(managementTarget, "-", "")
	for _, value := range []string{strings.ReplaceAll(hex, "0", "٠"), "+" + hex[1:], hex[:1] + "_" + hex[2:]} {
		f := managementFixtureNew()
		s := NewImpersonationManagement(f, f, f, time.Now, func() string { return managementSession })
		result, err := s.Start(context.Background(), &f.actor, domain.RequestObject{{Name: "target_user_id", Value: value}}, "", domain.LoginAuditContext{})
		if err != nil || result.Target.ID != managementTarget {
			t.Fatal("local UUID constructor rejected", value, err)
		}
		id, err := managementSessionKey(value)
		if err != nil || id != managementTarget {
			t.Fatal("session UUID constructor differs", id, err)
		}
	}
}

func TestImpersonationInvalidTargetTypesAreUnavailable(t *testing.T) {
	for _, value := range []any{nil, json.Number("1.5"), []any{}, domain.RequestObject{}, ""} {
		f := managementFixtureNew()
		s := NewImpersonationManagement(f, f, f, time.Now, func() string { return managementSession })
		_, err := s.Start(context.Background(), &f.actor, domain.RequestObject{{Name: "target_user_id", Value: value}}, "", domain.LoginAuditContext{})
		var denied *domain.PermissionDenied
		if !errors.As(err, &denied) || denied.Detail != "Utente non disponibile per l’impersonificazione." || len(f.events) != 0 || len(f.records) != 0 {
			t.Fatalf("invalid target %T: %v", value, err)
		}
	}
}

const managementActor = "00000000-0000-4000-8000-000000400001"
const managementTarget = "00000000-0000-4000-8000-000000400002"
const managementSession = "00000000-0000-4000-8000-000000400003"
const managementOld = "00000000-0000-4000-8000-000000400004"

type managementFixture struct {
	actor   domain.Account
	person  domain.ImpersonationPerson
	records map[string]domain.ImpersonationSession
	events  []domain.ImpersonationTransition
	calls   []string
	fail    map[string]error
}

func managementFixtureNew() *managementFixture {
	return &managementFixture{actor: domain.Account{ID: managementActor, Active: true, Superuser: true, Role: domain.RoleAssociation}, person: domain.ImpersonationPerson{ID: managementTarget, Username: "target", Role: domain.RoleAthlete}, records: map[string]domain.ImpersonationSession{}, fail: map[string]error{}}
}
func (f *managementFixture) ByID(context.Context, string) (domain.Account, error) {
	return f.actor, nil
}
func (f *managementFixture) ImpersonationAssociation(context.Context, string) (domain.ImpersonationAssociation, error) {
	return domain.ImpersonationAssociation{ID: "00000000-0000-4000-8000-000000400010", Name: "Owner association"}, nil
}
func (f *managementFixture) EligibleImpersonationPerson(context.Context, string, string, string) (domain.ImpersonationPerson, error) {
	return f.person, nil
}
func (f *managementFixture) ListImpersonationPeople(context.Context, string, string, domain.ImpersonationListFilter) ([]domain.ImpersonationPerson, int64, error) {
	return []domain.ImpersonationPerson{f.person}, 1, nil
}
func (f *managementFixture) ImpersonationPersonIncludingDeleted(context.Context, string) (domain.ImpersonationPerson, error) {
	return f.person, nil
}
func (f *managementFixture) AppendImpersonationTransition(_ context.Context, event domain.ImpersonationTransition) error {
	f.calls = append(f.calls, "audit:"+event.Action+":"+event.SessionID)
	if e := f.fail["audit:"+event.Action]; e != nil {
		return e
	}
	f.events = append(f.events, event)
	return nil
}
func (f *managementFixture) Get(_ context.Context, id string) (domain.ImpersonationSession, error) {
	f.calls = append(f.calls, "get:"+id)
	if e := f.fail["get:"+id]; e != nil {
		return domain.ImpersonationSession{}, e
	}
	v, ok := f.records[id]
	if !ok {
		return v, domain.ErrImpersonationNotFound
	}
	return v, nil
}
func (f *managementFixture) Put(_ context.Context, id string, v domain.ImpersonationSession) error {
	f.calls = append(f.calls, "put:"+id)
	if e := f.fail["put"]; e != nil {
		return e
	}
	f.records[id] = v
	return nil
}
func (f *managementFixture) Delete(_ context.Context, id string) error {
	f.calls = append(f.calls, "delete:"+id)
	if e := f.fail["delete"]; e != nil {
		return e
	}
	delete(f.records, id)
	return nil
}

func TestImpersonationStartAuditPrecedesCacheAndRetainsFailureEffects(t *testing.T) {
	for _, stage := range []string{"success", "audit:started", "put"} {
		t.Run(stage, func(t *testing.T) {
			f := managementFixtureNew()
			failure := errors.New("owned failure")
			if stage != "success" {
				f.fail[stage] = failure
			}
			s := NewImpersonationManagement(f, f, f, time.Now, func() string { return managementSession })
			result, err := s.Start(context.Background(), &f.actor, domain.RequestObject{{Name: "target_user_id", Value: managementTarget}}, "", domain.LoginAuditContext{})
			if stage == "success" {
				if err != nil || result.SessionID != managementSession || result.ExpiresIn != 3600 || result.ActorRole != "administrator" || result.Association != nil {
					t.Fatal(result, err)
				}
			} else if !errors.Is(err, failure) {
				t.Fatal(err)
			}
			wantAudit := 1
			if stage == "audit:started" {
				wantAudit = 0
			}
			if len(f.events) != wantAudit {
				t.Fatal("audit durability boundary changed")
			}
			_, stored := f.records[managementSession]
			if stored != (stage == "success") {
				t.Fatal("cache transition differs")
			}
			want := []string{"audit:started:" + managementSession}
			if stage != "audit:started" {
				want = append(want, "put:"+managementSession)
			}
			if !reflect.DeepEqual(f.calls, want) {
				t.Fatal(f.calls, want)
			}
		})
	}
}

func TestImpersonationReplacementCompensatesNewSession(t *testing.T) {
	f := managementFixtureNew()
	failure := errors.New("old cache unavailable")
	f.fail["get:"+managementOld] = failure
	s := NewImpersonationManagement(f, f, f, time.Now, func() string { return managementSession })
	_, err := s.Start(context.Background(), &f.actor, domain.RequestObject{{Name: "target_user_id", Value: managementTarget}}, managementOld, domain.LoginAuditContext{})
	if !errors.Is(err, failure) || len(f.records) != 0 || len(f.events) != 2 || f.events[1].Action != "ended" {
		t.Fatal(err, f.records, f.events)
	}
	want := []string{"audit:started:" + managementSession, "put:" + managementSession, "get:" + managementOld, "get:" + managementSession, "audit:ended:" + managementSession, "delete:" + managementSession}
	if !reflect.DeepEqual(f.calls, want) {
		t.Fatal(f.calls, want)
	}
}

func TestImpersonationEndCannotRevokeAnotherActorAndAuditFailureKeepsSession(t *testing.T) {
	for _, foreign := range []bool{true, false} {
		t.Run(map[bool]string{true: "foreign", false: "audit-failure"}[foreign], func(t *testing.T) {
			f := managementFixtureNew()
			actor := managementActor
			if foreign {
				actor = managementTarget
			} else {
				f.fail["audit:ended"] = errors.New("audit unavailable")
			}
			f.records[managementSession] = domain.ImpersonationSession{ActorID: actor, TargetID: managementTarget}
			s := NewImpersonationManagement(f, f, f, time.Now, func() string { return managementSession })
			if err := s.End(context.Background(), &f.actor, managementSession, domain.LoginAuditContext{}); err == nil {
				t.Fatal("expected denial/failure")
			}
			if len(f.records) != 1 || len(f.events) != 0 {
				t.Fatal("failed termination changed state")
			}
		})
	}
}

func TestImpersonationListParsingAndPublicProjection(t *testing.T) {
	f := managementFixtureNew()
	s := NewImpersonationManagement(f, f, f, time.Now, func() string { return managementSession })
	for _, query := range []domain.RequestObject{
		{{Name: "page", Value: "0"}}, {{Name: "pagination[perpage]", Value: "101"}}, {{Name: "page", Value: "1.0"}},
		{{Name: "role", Value: "administrator"}}, {{Name: "sort[field]", Value: "password"}}, {{Name: "sort[sort]", Value: "DESC"}},
	} {
		if _, err := s.List(context.Background(), &f.actor, query); err == nil {
			t.Fatal("invalid list query accepted", query)
		}
	}
	result, err := s.List(context.Background(), &f.actor, domain.RequestObject{{Name: "pagination[page]", Value: " +٠١ "}, {Name: "page", Value: "0"}})
	if err != nil || result.Meta.Page != "1" || len(result.Users) != 1 || result.Users[0].Role != "athlete" || result.Users[0].LastLogin != nil || result.NextPage != nil {
		t.Fatal(result, err)
	}
}
