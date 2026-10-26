package service

import (
	"context"
	"errors"
	"reflect"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

type messageReadsStoreTest struct {
	list, transactions []domain.MessageReadRecord
	details            []domain.MessageTransactionReadRecord
	listOwner          string
	detailOwner, id    string
	listCalls          int
	detailCalls        int
	err                error
}

func (s *messageReadsStoreTest) ListMessages(_ context.Context, owner string) ([]domain.MessageReadRecord, error) {
	s.listCalls++
	s.listOwner = owner
	return s.list, s.err
}

func (s *messageReadsStoreTest) MessageTransactions(_ context.Context, owner, id string) ([]domain.MessageTransactionReadRecord, error) {
	s.detailCalls++
	s.detailOwner, s.id = owner, id
	return s.details, s.err
}

func messageReadsAccount(id string, role int) *domain.Account {
	return &domain.Account{ID: id, Role: role, Active: true}
}

func TestMessageReadsUsesTrustedTenantScopeAndPreservesEmptyResponses(t *testing.T) {
	owner := messageReadsAccount("a1000000-0000-4000-8000-000000700001", domain.RoleAssociation)
	collaborator := messageReadsAccount("a1000000-0000-4000-8000-000000700002", domain.RoleCollaborator)
	messageID := uuid.MustParse("a1000000-0000-4000-8000-000000702001")
	associationID := uuid.MustParse("a1000000-0000-4000-8000-000000701001")
	created := time.Date(2026, 10, 23, 8, 9, 10, 123456000, time.UTC)
	store := &messageReadsStoreTest{list: []domain.MessageReadRecord{{
		ID: messageID, AssociationID: associationID, Type: "EMAIL", Message: "Avviso", CreatedAt: &created,
	}}}
	identity := domain.ResolvedIdentity{
		Actor: collaborator, Effective: collaborator, ScopeUser: owner, Collaborator: true,
	}
	flow := NewMessageReads(store)
	result, err := flow.List(context.Background(), identity)
	if err != nil || len(result) != 1 || result[0].ID != messageID || store.listOwner != owner.ID {
		t.Fatalf("list: %+v %+v %v", result, store, err)
	}
	if result[0].Subject != "None" {
		t.Fatal(result[0])
	}
	if _, err = flow.Detail(context.Background(), identity, messageID.String()); !errors.Is(err, domain.ErrMessageReadNotFound) {
		t.Fatal(err)
	}
	if store.detailOwner != owner.ID || store.id != messageID.String() {
		t.Fatal("detail scope/IP", store)
	}
	store.list, store.details = nil, nil
	emptyList, err := flow.List(context.Background(), identity)
	if err != nil || emptyList == nil || len(emptyList) != 0 {
		t.Fatalf("Django list must encode [] rather than null: %#v %v", emptyList, err)
	}
}

func TestMessageReadsRoleUUIDAdmissionAndCancellation(t *testing.T) {
	owner := messageReadsAccount("a1000000-0000-4000-8000-000000700001", domain.RoleAssociation)
	athlete := messageReadsAccount("a1000000-0000-4000-8000-000000700004", domain.RoleAthlete)
	store := &messageReadsStoreTest{}
	flow := NewMessageReads(store)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := flow.List(ctx, domain.ResolvedIdentity{ScopeUser: owner}); !errors.Is(err, context.Canceled) || store.listCalls != 0 {
		t.Fatal("cancelled list", err, store.listCalls)
	}
	if _, err := flow.Detail(ctx, domain.ResolvedIdentity{ScopeUser: owner}, uuid.NewString()); !errors.Is(err, context.Canceled) || store.detailCalls != 0 {
		t.Fatal("cancelled detail", err, store.detailCalls)
	}
	if _, err := flow.List(context.Background(), domain.ResolvedIdentity{ScopeUser: athlete}); !errors.Is(err, domain.ErrMessageReadForbidden) || store.listCalls != 0 {
		t.Fatal("athlete", err, store.listCalls)
	}
	var invalid *dto.MessageReadUUIDError
	if _, err := flow.Detail(context.Background(), domain.ResolvedIdentity{ScopeUser: owner}, "not-a-uuid"); !errors.As(err, &invalid) ||
		invalid.Detail != "badly formed hexadecimal UUID string" || store.detailCalls != 0 {
		t.Fatal("invalid UUID", err, store.detailCalls)
	}
	for _, raw := range []string{upperCase(messageIDForMessageReads()), "{" + messageIDForMessageReads() + "}", "urn:uuid:" + messageIDForMessageReads()} {
		store.details = []domain.MessageTransactionReadRecord{{ID: uuid.New(), MessageID: uuid.MustParse(messageIDForMessageReads()), SentOn: time.Now()}}
		if _, err := flow.Detail(context.Background(), domain.ResolvedIdentity{ScopeUser: owner}, raw); err != nil || store.id != messageIDForMessageReads() {
			t.Fatal(raw, err, store.id)
		}
	}
}

func TestMessageReadsAdmissionUsesTrustedScopeBeforeReads(t *testing.T) {
	owner := messageReadsAccount("a1000000-0000-4000-8000-000000700001", domain.RoleAssociation)
	athlete := messageReadsAccount("a1000000-0000-4000-8000-000000700004", domain.RoleAthlete)
	collaborator := messageReadsAccount("a1000000-0000-4000-8000-000000700002", domain.RoleCollaborator)
	store := &messageReadsStoreTest{}
	flow := NewMessageReads(store)

	for _, identity := range []domain.ResolvedIdentity{
		{Actor: owner, Effective: owner, ScopeUser: owner},
		{Actor: collaborator, Effective: collaborator, ScopeUser: owner, Collaborator: true},
		// Actor and effective identities do not authorize business data. The
		// trusted tenant scope remains decisive after the shared resolver.
		{Actor: athlete, Effective: athlete, ScopeUser: owner},
	} {
		if err := flow.Admit(context.Background(), identity); err != nil {
			t.Fatalf("trusted scope denied: %+v", identity)
		}
	}
	for _, identity := range []domain.ResolvedIdentity{
		{Actor: owner, Effective: owner, ScopeUser: athlete},
		{Actor: owner, Effective: athlete, ScopeUser: athlete, Impersonating: true},
		{Actor: owner, Effective: owner},
	} {
		if err := flow.Admit(context.Background(), identity); !errors.Is(err, domain.ErrMessageReadForbidden) {
			t.Fatalf("unsafe scope admitted: %+v: %v", identity, err)
		}
	}
	if store.listCalls != 0 || store.detailCalls != 0 {
		t.Fatal("permission admission read message data")
	}

	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := flow.Admit(ctx, domain.ResolvedIdentity{Actor: owner, Effective: owner, ScopeUser: owner}); !errors.Is(err, context.Canceled) {
		t.Fatal("cancelled admission", err)
	}
}

func TestMessageReadsDetailReturnsTypedTransactionsInStoreOrder(t *testing.T) {
	owner := messageReadsAccount("a1000000-0000-4000-8000-000000700001", domain.RoleAssociation)
	messageID := uuid.MustParse("a1000000-0000-4000-8000-000000702001")
	first := uuid.MustParse("a1000000-0000-4000-8000-000000703001")
	second := uuid.MustParse("a1000000-0000-4000-8000-000000703002")
	store := &messageReadsStoreTest{details: []domain.MessageTransactionReadRecord{
		{ID: first, MessageID: messageID, Recipient: "first@example.test", SentOn: time.Date(2026, 10, 23, 12, 0, 0, 0, time.UTC)},
		{ID: second, MessageID: messageID, Recipient: "second@example.test", SentOn: time.Date(2026, 10, 23, 11, 0, 0, 0, time.UTC)},
	}}
	got, err := NewMessageReads(store).Detail(context.Background(), domain.ResolvedIdentity{ScopeUser: owner}, messageID.String())
	if err != nil || !reflect.DeepEqual([]uuid.UUID{got[0].ID, got[1].ID}, []uuid.UUID{first, second}) ||
		got[0].Recipient != "first@example.test" || got[1].Recipient != "second@example.test" {
		t.Fatalf("%+v %v", got, err)
	}
}

func messageIDForMessageReads() string {
	return "a1000000-0000-4000-8000-000000702001"
}

func upperCase(value string) string {
	result := []byte(value)
	for i, character := range result {
		if character >= 'a' && character <= 'f' {
			result[i] = character - ('a' - 'A')
		}
	}
	return string(result)
}
