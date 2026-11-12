package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// formRenewTransaction is the renewal transaction of a form-body request: only the
// refusal paths are exercised, so the remaining methods stay unimplemented (a call
// on them panics, failing the test).
type formRenewTransaction struct {
	domain.SubscriptionRenewTransaction
	associationCount int
	connectedCount   int
	owner            *domain.Account
	allowed          bool
	rollbacks        int
}

func (t *formRenewTransaction) AssociationCount(context.Context, string) (int, error) {
	return t.associationCount, nil
}

func (t *formRenewTransaction) RenewAssociationOwner(context.Context, string) (*domain.Account, error) {
	return t.owner, nil
}

func (t *formRenewTransaction) MemberRenewable(context.Context, string, string, *string, *string) (bool, error) {
	return t.allowed, nil
}

func (t *formRenewTransaction) ConnectedAssociationCount(context.Context, string) (int, error) {
	return t.connectedCount, nil
}

func (t *formRenewTransaction) SetGroup(*string) {}

func (t *formRenewTransaction) Rollback() error {
	t.rollbacks++
	return nil
}

type formRenewStore struct{ tx *formRenewTransaction }

func (s formRenewStore) BeginSubscriptionRenew(context.Context) (domain.SubscriptionRenewTransaction, error) {
	return s.tx, nil
}

func formRenewIdentity(role int) domain.ResolvedIdentity {
	actor := &domain.Account{ID: "10000000-0000-4000-8000-000000000001", Email: "form@example.test", Role: domain.RoleAssociation}
	return domain.ResolvedIdentity{Actor: actor, Effective: actor, ScopeUser: &domain.Account{ID: actor.ID, Role: role}}
}

func newFormRenewals(tx *formRenewTransaction) *SubscriptionRenewals {
	now := func() time.Time { return time.Date(2026, time.November, 12, 9, 0, 0, 0, time.UTC) }
	creates := NewSubscriptionCreates(nil, nil, nil, now, nil)
	return NewSubscriptionRenewals(formRenewStore{tx: tx}, creates)
}

func renewFailureText(t *testing.T, err error) string {
	t.Helper()
	var failure *SubscriptionRenewFailure
	if !errors.As(err, &failure) {
		t.Fatalf("want *SubscriptionRenewFailure, got %v", err)
	}
	return failure.Text
}

func serviceAssociationID(t *testing.T) string {
	t.Helper()
	parsed, err := paymentInfoParseUUID("a1000000-0000-4000-8000-000000953001")
	if err != nil {
		t.Fatalf("parse association id: %v", err)
	}
	return parsed.String()
}

func TestRenewFormBodyAssociateDataText(t *testing.T) {
	tx := &formRenewTransaction{associationCount: 1}
	body := domain.RequestObject{
		{Name: "associate_data", Value: "x"},
		{Name: "new_user_account", Value: "y"},
		{Name: "signature", Value: "z"},
	}
	_, err := newFormRenewals(tx).Renew(context.Background(), formRenewIdentity(domain.RoleAssociation),
		SubscriptionCreateRequest{Body: body, Form: true})
	if text := renewFailureText(t, err); text != "string indices must be integers, not 'str'" {
		t.Fatalf("unexpected text: %q", text)
	}
	if tx.rollbacks != 1 {
		t.Fatalf("transaction rolled back %d times", tx.rollbacks)
	}
}

func TestRenewFormBodyEmpty(t *testing.T) {
	tx := &formRenewTransaction{associationCount: 1}
	_, err := newFormRenewals(tx).Renew(context.Background(), formRenewIdentity(domain.RoleAssociation),
		SubscriptionCreateRequest{Body: domain.RequestObject{}, Form: true})
	if text := renewFailureText(t, err); text != "'associate_data'" {
		t.Fatalf("unexpected text: %q", text)
	}
}

func TestRenewFormBodyPreregistrationIsText(t *testing.T) {
	// A form's preregistration is the text "True", never `is True`: no next-season branch.
	tx := &formRenewTransaction{associationCount: 1}
	body := domain.RequestObject{
		{Name: "preregistration", Value: "True"},
		{Name: "associate_data", Value: "x"},
	}
	_, err := newFormRenewals(tx).Renew(context.Background(), formRenewIdentity(domain.RoleAssociation),
		SubscriptionCreateRequest{Body: body, Form: true})
	if text := renewFailureText(t, err); text != "string indices must be integers, not 'str'" {
		t.Fatalf("unexpected text: %q", text)
	}
}

func TestRenewFormBodyFileAssociateData(t *testing.T) {
	for name, upload := range map[string]domain.RequestUpload{
		"InMemoryUploadedFile":  {Name: "associate.json", Size: 21, Content: `{"born_date":"1990"}`, Whole: true},
		"TemporaryUploadedFile": {Name: "associate.json", Size: domain.RequestUploadMemory + 1, Temp: true},
	} {
		t.Run(name, func(t *testing.T) {
			tx := &formRenewTransaction{associationCount: 1}
			body := domain.RequestObject{{Name: "associate_data", Value: upload}}
			_, err := newFormRenewals(tx).Renew(context.Background(), formRenewIdentity(domain.RoleAssociation),
				SubscriptionCreateRequest{Body: body, Form: true})
			if text := renewFailureText(t, err); text != "'"+name+"' object is not subscriptable" {
				t.Fatalf("unexpected text: %q", text)
			}
		})
	}
}

func TestRenewFormBodyMemberAssociationText(t *testing.T) {
	// data['sport_association']['sport_association_id'] runs outside the try block.
	tx := &formRenewTransaction{associationCount: 1}
	body := domain.RequestObject{{Name: "sport_association", Value: "associazione"}}
	_, err := newFormRenewals(tx).Renew(context.Background(), formRenewIdentity(domain.RoleAthlete),
		SubscriptionCreateRequest{Body: body, Form: true})
	if !errors.Is(err, domain.ErrSubscriptionCreateServer) {
		t.Fatalf("want the server error, got %v", err)
	}
	_, err = newFormRenewals(tx).Renew(context.Background(), formRenewIdentity(domain.RoleAthlete),
		SubscriptionCreateRequest{Body: domain.RequestObject{}, Form: true})
	if !errors.Is(err, domain.ErrSubscriptionCreateServer) {
		t.Fatalf("want the server error for a missing key, got %v", err)
	}
}

func memberRenewalBody(t *testing.T) domain.RequestObject {
	t.Helper()
	associate, err := paymentInfoParseUUID("a1000000-0000-4000-8000-000000953002")
	if err != nil {
		t.Fatalf("parse associate id: %v", err)
	}
	return domain.RequestObject{
		{Name: "sport_association", Value: domain.RequestObject{{Name: "sport_association_id", Value: serviceAssociationID(t)}}},
		{Name: "associate_data", Value: domain.RequestObject{{Name: "associate_id", Value: associate.String()}}},
	}
}

func TestRenewAssociationOwnedByAthlete(t *testing.T) {
	tx := &formRenewTransaction{associationCount: 1, allowed: true,
		owner: &domain.Account{ID: "10000000-0000-4000-8000-000000000004", Role: domain.RoleAthlete}}
	_, err := newFormRenewals(tx).Renew(context.Background(), formRenewIdentity(domain.RoleAthlete),
		SubscriptionCreateRequest{Body: memberRenewalBody(t), Form: true})
	var denied *domain.PermissionDenied
	if !errors.As(err, &denied) || denied.Detail != "User is not a sport association" {
		t.Fatalf("want the athlete association detail, got %v", err)
	}
}

func TestRenewAssociationOwnedByCollaborator(t *testing.T) {
	owner := &domain.Account{ID: "10000000-0000-4000-8000-000000000003", Role: domain.RoleCollaborator}
	for name, count := range map[string]int{"none": 0, "several": 2} {
		t.Run(name, func(t *testing.T) {
			tx := &formRenewTransaction{associationCount: 1, allowed: true, owner: owner, connectedCount: count}
			_, err := newFormRenewals(tx).Renew(context.Background(), formRenewIdentity(domain.RoleAthlete),
				SubscriptionCreateRequest{Body: memberRenewalBody(t), Form: true})
			want := "SportAssociation matching query does not exist."
			if count != 0 {
				want = "get() returned more than one SportAssociation -- it returned 2!"
			}
			if text := renewFailureText(t, err); text != want {
				t.Fatalf("unexpected text: %q", text)
			}
		})
	}
	t.Run("one", func(t *testing.T) {
		tx := &formRenewTransaction{associationCount: 1, allowed: true, owner: owner, connectedCount: 1}
		_, err := newFormRenewals(tx).Renew(context.Background(), formRenewIdentity(domain.RoleAthlete),
			SubscriptionCreateRequest{Body: memberRenewalBody(t), Form: true})
		if !errors.Is(err, domain.ErrSubscriptionCreateNotPorted) {
			t.Fatalf("want the not-ported refusal, got %v", err)
		}
	})
}

func TestRenewAcceptsAGroupHeader(t *testing.T) {
	// The header is applied to the transaction; an empty body reaches the shared
	// flow's first read instead of the removed header refusal.
	tx := &formRenewTransaction{associationCount: 1}
	_, err := newFormRenewals(tx).Renew(context.Background(), formRenewIdentity(domain.RoleAssociation),
		SubscriptionCreateRequest{Body: domain.RequestObject{}, Form: true, Group: "group"})
	if text := renewFailureText(t, err); text != "'associate_data'" {
		t.Fatalf("unexpected text: %q", text)
	}
	if tx.rollbacks != 1 {
		t.Fatalf("transaction rolled back %d times", tx.rollbacks)
	}
}

func TestApproveFormBody(t *testing.T) {
	approvals := NewSubscriptionDraftApprovals(nil, nil)
	cases := []struct {
		name string
		body domain.RequestObject
		want error
	}{
		{"empty", domain.RequestObject{}, ErrDraftApproveMissingIDs},
		{"text details", domain.RequestObject{{Name: "associate_import_draft_ids", Value: "x"},
			{Name: "subscription_details", Value: "y"}}, domain.ErrSubscriptionCreateServer},
		{"missing details", domain.RequestObject{{Name: "associate_import_draft_ids", Value: "x"}},
			domain.ErrSubscriptionCreateServer},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			_, err := approvals.Approve(context.Background(), formRenewIdentity(domain.RoleAssociation),
				SubscriptionCreateRequest{Body: test.body, Form: true})
			if !errors.Is(err, test.want) {
				t.Fatalf("want %v, got %v", test.want, err)
			}
		})
	}
	t.Run("athlete", func(t *testing.T) {
		_, err := approvals.Approve(context.Background(), formRenewIdentity(domain.RoleAthlete),
			SubscriptionCreateRequest{Body: domain.RequestObject{{Name: "associate_import_draft_ids", Value: "x"}}, Form: true})
		if !errors.Is(err, domain.ErrAssociateDraftUnauthorized) {
			t.Fatalf("want the unauthorized refusal, got %v", err)
		}
	})
	t.Run("group", func(t *testing.T) {
		_, err := approvals.Approve(context.Background(), formRenewIdentity(domain.RoleAssociation),
			SubscriptionCreateRequest{Body: domain.RequestObject{}, Form: true, Group: "group"})
		if !errors.Is(err, ErrDraftApproveMissingIDs) {
			t.Fatalf("want the missing-ids refusal, got %v", err)
		}
	})
}
