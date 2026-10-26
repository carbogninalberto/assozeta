package service

import (
	"context"
	"errors"
	"reflect"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type messageMutationFake struct {
	calls            []string
	fail             string
	record           domain.MessageMutationState
	audit            domain.MessageMutationAudit
	rollback, commit bool
	cancel           context.CancelFunc
}

func (f *messageMutationFake) step(s string) error {
	f.calls = append(f.calls, s)
	if f.fail == s {
		return errors.New(s)
	}
	return nil
}
func (f *messageMutationFake) BeginMessageMutations(context.Context) (domain.MessageMutationTransaction, error) {
	return f, f.step("begin")
}
func (f *messageMutationFake) OwnerForUpdate(context.Context, string) (int, bool, bool, error) {
	return domain.RoleAssociation, true, false, f.step("owner")
}
func (f *messageMutationFake) AssociationForOwner(context.Context, string, *string) (string, error) {
	return "assoc", f.step("association")
}
func (f *messageMutationFake) MessageForUpdate(context.Context, string, string) (domain.MessageMutationState, error) {
	return f.record, f.step("lock")
}
func (f *messageMutationFake) InsertMessage(_ context.Context, s domain.MessageMutationState) error {
	f.record = s
	return f.step("insert")
}
func (f *messageMutationFake) DeleteMessageDeliveries(context.Context, string) error {
	return f.step("deliveries")
}
func (f *messageMutationFake) DeleteMessage(context.Context, string, string) error {
	return f.step("delete")
}
func (f *messageMutationFake) AppendMessageAudit(_ context.Context, a domain.MessageMutationAudit) error {
	f.audit = a
	if f.cancel != nil {
		f.cancel()
	}
	return f.step("audit")
}
func (f *messageMutationFake) Commit() error {
	if err := f.step("commit"); err != nil {
		return err
	}
	f.commit = true
	return nil
}
func (f *messageMutationFake) Rollback() error { f.rollback = true; return nil }
func messageMutationIdentity() domain.ResolvedIdentity {
	a := &domain.Account{ID: "owner", Email: "actor@example.test", Role: domain.RoleAssociation, Active: true}
	return domain.ResolvedIdentity{Actor: a, Effective: a, ScopeUser: a}
}

const messageMutationID = "a1000000-0000-4000-8000-000000702001"

func TestMessageMutationsCreateAtomicAndAudit(t *testing.T) {
	at := time.Date(2026, 10, 26, 8, 9, 10, 123456789, time.UTC)
	f := &messageMutationFake{}
	s := NewMessageMutations(f, func() time.Time { return at })
	input := dto.MessageMutationWrite{Type: "EMAIL", Message: " <b> preserved </b> "}
	if err := s.Add(context.Background(), messageMutationIdentity(), input, domain.LoginAuditContext{}); err != nil {
		t.Fatal(err)
	}
	if !f.commit || !f.rollback || f.record.AssociationID != "assoc" || f.record.Message != input.Message || f.record.Subject != nil || f.record.ID == "" || !f.record.CreatedAt.Equal(at.Truncate(time.Microsecond)) {
		t.Fatalf("state %#v fake %#v", f.record, f)
	}
	if f.audit.Action != 0 || f.audit.Changes["message"] != [2]string{"None", input.Message} || f.audit.Context.ActorID == nil || *f.audit.Context.ActorID != "owner" || f.audit.ResolutionPath != "direct" {
		t.Fatalf("audit %#v", f.audit)
	}
	if _, ok := f.audit.Changes["subject"]; ok {
		t.Fatal("null subject must be omitted from audit diff")
	}
	if _, ok := f.audit.Changes["messagetransaction"]; ok {
		t.Fatal("default reverse accessor is absent from audit diff")
	}
}
func TestMessageMutationsDeleteOrderRollbackAndScope(t *testing.T) {
	for _, fail := range []string{"", "owner", "association", "lock", "deliveries", "delete", "audit", "commit"} {
		t.Run(fail, func(t *testing.T) {
			f := &messageMutationFake{fail: fail, record: domain.MessageMutationState{ID: messageMutationID, AssociationID: "assoc", Type: "LEGACY", Message: "body"}}
			err := NewMessageMutations(f, time.Now).Delete(context.Background(), messageMutationIdentity(), messageMutationID, domain.LoginAuditContext{})
			if (err == nil) != (fail == "") || !f.rollback || f.commit != (fail == "") {
				t.Fatalf("error %v calls %v committed %v rollback %v", err, f.calls, f.commit, f.rollback)
			}
			if fail == "" {
				want := []string{"begin", "owner", "association", "lock", "deliveries", "delete", "audit", "commit"}
				if !reflect.DeepEqual(f.calls, want) {
					t.Fatal(f.calls)
				}
				if f.audit.Action != 2 || f.audit.ResolutionPath != "changes_json.sport_association" || f.audit.Changes["message"] != [2]string{"body", "None"} {
					t.Fatalf("audit %#v", f.audit)
				}
			}
		})
	}
	f := &messageMutationFake{record: domain.MessageMutationState{ID: messageMutationID, AssociationID: "foreign"}}
	err := NewMessageMutations(f, time.Now).Delete(context.Background(), messageMutationIdentity(), messageMutationID, domain.LoginAuditContext{})
	if !errors.Is(err, domain.ErrMessageMutationNotFound) || f.commit {
		t.Fatalf("foreign: %v", err)
	}
}
func TestMessageMutationsAuthorizationValidationAndCancellation(t *testing.T) {
	f := &messageMutationFake{}
	s := NewMessageMutations(f, time.Now)
	bad := messageMutationIdentity()
	bad.ScopeUser = &domain.Account{ID: "athlete", Role: domain.RoleAthlete}
	if err := s.Add(context.Background(), bad, dto.MessageMutationWrite{Type: "EMAIL", Message: "x"}, domain.LoginAuditContext{}); err == nil || len(f.calls) != 0 {
		t.Fatalf("auth %v %v", err, f.calls)
	}
	if err := s.Add(context.Background(), messageMutationIdentity(), dto.MessageMutationWrite{Type: "SMS", Message: "x"}, domain.LoginAuditContext{}); !errors.Is(err, domain.ErrMessageMutationInvalid) || f.commit {
		t.Fatalf("invalid %v", err)
	}
	f = &messageMutationFake{}
	ctx, cancel := context.WithCancel(context.Background())
	f.cancel = cancel
	err := NewMessageMutations(f, time.Now).Add(ctx, messageMutationIdentity(), dto.MessageMutationWrite{Type: "EMAIL", Message: "x"}, domain.LoginAuditContext{})
	if !errors.Is(err, context.Canceled) || f.commit || !f.rollback {
		t.Fatalf("cancel %v %#v", err, f)
	}
	f = &messageMutationFake{}
	ctx, cancel = context.WithCancel(context.Background())
	cancel()
	if err = NewMessageMutations(f, time.Now).Delete(ctx, messageMutationIdentity(), messageMutationID, domain.LoginAuditContext{}); !errors.Is(err, context.Canceled) || len(f.calls) != 0 {
		t.Fatal(err, f.calls)
	}
}

// Expected from production auditlog.diff: CharField/TextField values literally
// equal to "None" compare equal to the absent side and are omitted. A null
// DateTimeField compares Python None against the absent-side string "None",
// then stringifies both, retaining ["None","None"].
func TestMessageMutationsAuditLiteralNoneAndNullDatetime(t *testing.T) {
	subject := "None"
	state := domain.MessageMutationState{ID: messageMutationID, AssociationID: "assoc", Type: "LEGACY", Message: "None", Subject: &subject}
	event := messageMutationAudit(state, 2, time.Now(), domain.LoginAuditContext{})
	if _, exists := event.Changes["message"]; exists {
		t.Fatalf("literal None message must be omitted: %#v", event.Changes)
	}
	if _, exists := event.Changes["subject"]; exists {
		t.Fatalf("literal None subject must be omitted: %#v", event.Changes)
	}
	if value, exists := event.Changes["created_at"]; !exists || value != [2]string{"None", "None"} {
		t.Fatalf("null datetime diff: %#v", event.Changes)
	}
}

func TestMessageMutationsCreateFailuresDoNotCommit(t *testing.T) {
	for _, failure := range []string{"begin", "owner", "association", "insert", "audit", "commit"} {
		t.Run(failure, func(t *testing.T) {
			f := &messageMutationFake{fail: failure}
			err := NewMessageMutations(f, time.Now).Add(context.Background(), messageMutationIdentity(), dto.MessageMutationWrite{Type: "EMAIL", Message: "body"}, domain.LoginAuditContext{})
			if err == nil || f.commit || f.rollback != (failure != "begin") {
				t.Fatalf("err=%v commit=%v rollback=%v calls=%v", err, f.commit, f.rollback, f.calls)
			}
		})
	}
}
func TestMessageMutationsTrustedIdentityAndImpersonationAudit(t *testing.T) {
	for _, change := range []func(*domain.ResolvedIdentity){func(i *domain.ResolvedIdentity) { i.Actor = nil }, func(i *domain.ResolvedIdentity) { i.Effective = nil }, func(i *domain.ResolvedIdentity) { i.ScopeUser = nil }, func(i *domain.ResolvedIdentity) { i.Collaborator = true }, func(i *domain.ResolvedIdentity) {
		owner := "foreign"
		i.Collaborator = true
		i.Effective = &domain.Account{ID: "collaborator", Role: domain.RoleCollaborator, ConnectedUserID: &owner}
	}} {
		f := &messageMutationFake{}
		identity := messageMutationIdentity()
		change(&identity)
		if err := NewMessageMutations(f, time.Now).Add(context.Background(), identity, dto.MessageMutationWrite{Type: "EMAIL", Message: "body"}, domain.LoginAuditContext{}); !errors.Is(err, domain.ErrInvalidScopedRequest) || len(f.calls) != 0 {
			t.Fatalf("identity %#v: %v calls %v", identity, err, f.calls)
		}
	}
	identity := messageMutationIdentity()
	identity.Actor = &domain.Account{ID: "admin", Email: "admin@example.test", Role: domain.RoleAssociation, Superuser: true, Active: true}
	identity.Impersonating = true
	identity.SessionID = "session"
	identity.ImpersonationAssociationID = "assoc"
	forged := "forged"
	f := &messageMutationFake{}
	if err := NewMessageMutations(f, time.Now).Add(context.Background(), identity, dto.MessageMutationWrite{Type: "EMAIL", Message: "body"}, domain.LoginAuditContext{ActorID: &forged}); err != nil {
		t.Fatal(err)
	}
	if f.audit.Context.ActorID == nil || *f.audit.Context.ActorID != "admin" || f.audit.Context.AdditionalData == nil || f.audit.Context.AdditionalData.TargetID != "owner" || f.audit.Context.AdditionalData.AssociationID == nil || *f.audit.Context.AdditionalData.AssociationID != "assoc" {
		t.Fatalf("audit %#v", f.audit.Context)
	}
}

func TestMessageMutationsInvalidUUIDBeforeTransaction(t *testing.T) {
	f := &messageMutationFake{}
	err := NewMessageMutations(f, time.Now).Delete(context.Background(), messageMutationIdentity(), "invalid", domain.LoginAuditContext{})
	var invalid *dto.MessageReadUUIDError
	if !errors.As(err, &invalid) || invalid.Detail != "badly formed hexadecimal UUID string" || len(f.calls) != 0 {
		t.Fatalf("error %v calls %v", err, f.calls)
	}
}
