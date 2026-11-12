package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jackc/pgx/v5/pgconn"
)

func TestSubscriptionRenewGroupAppliesOnlyAUUID(t *testing.T) {
	for _, test := range []struct {
		name    string
		header  string
		query   *string
		invalid bool
	}{
		{"no header", "", nil, false},
		{"blank header", "  ", nil, false},
		{"canonical", "a1000000-0000-4000-8000-000000714001", groupPtr("a1000000-0000-4000-8000-000000714001"), false},
		{"other spelling", "A1000000-0000-4000-8000-000000714001", groupPtr("a1000000-0000-4000-8000-000000714001"), false},
		{"not a uuid", "zzz", nil, true},
	} {
		t.Run(test.name, func(t *testing.T) {
			group, _ := subscriptionCreateGroup(test.header)
			query, invalid := subscriptionRenewGroup(group)
			if invalid != test.invalid {
				t.Fatalf("invalid = %v, want %v", invalid, test.invalid)
			}
			if (query == nil) != (test.query == nil) || (query != nil && *query != *test.query) {
				t.Fatalf("query = %v, want %v", query, test.query)
			}
		})
	}
}

func groupPtr(text string) *string { return &text }

func TestSubscriptionRenewDatabaseErrorMatchesPsycopgText(t *testing.T) {
	cases := []struct {
		name string
		err  *pgconn.PgError
		want string
	}{
		{"detail", &pgconn.PgError{Message: "insert or update on table \"application_payment\" violates foreign key constraint \"f\"",
			Detail: "Key (group_id)=(g) is not present in table \"application_group\"."},
			"insert or update on table \"application_payment\" violates foreign key constraint \"f\"\nDETAIL:  Key (group_id)=(g) is not present in table \"application_group\".\n"},
		{"message only", &pgconn.PgError{Message: "boom"}, "boom\n"},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			err := subscriptionRenewDatabaseError(test.err)
			var failure *SubscriptionRenewFailure
			if !errors.As(err, &failure) || failure.Text != test.want {
				t.Fatalf("error = %v, want %q", err, test.want)
			}
		})
	}
}

// groupRenewTransaction records the group the renewal applies; the paths under
// test stop before the shared create flow needs the other methods.
type groupRenewTransaction struct {
	domain.SubscriptionRenewTransaction
	associationCount int
	group            *string
	rollbacks        int
}

func (t *groupRenewTransaction) SetGroup(group *string) { t.group = group }

func (t *groupRenewTransaction) AssociationCount(context.Context, string) (int, error) {
	return t.associationCount, nil
}

func (t *groupRenewTransaction) Rollback() error {
	t.rollbacks++
	return nil
}

type groupRenewStore struct{ tx *groupRenewTransaction }

func (s groupRenewStore) BeginSubscriptionRenew(context.Context) (domain.SubscriptionRenewTransaction, error) {
	return s.tx, nil
}

func newGroupRenewals(tx *groupRenewTransaction) *SubscriptionRenewals {
	now := func() time.Time { return time.Date(2026, time.November, 12, 9, 0, 0, 0, time.UTC) }
	return NewSubscriptionRenewals(groupRenewStore{tx: tx}, NewSubscriptionCreates(nil, nil, nil, now, nil))
}

func groupRenewIdentity() domain.ResolvedIdentity {
	actor := &domain.Account{ID: "10000000-0000-4000-8000-000000000001", Email: "group@example.test", Role: domain.RoleAssociation}
	return domain.ResolvedIdentity{Actor: actor, Effective: actor, ScopeUser: &domain.Account{ID: actor.ID, Role: domain.RoleAssociation}}
}

func TestRenewPassesAValidGroupToTheTransaction(t *testing.T) {
	tx := &groupRenewTransaction{associationCount: 1}
	_, err := newGroupRenewals(tx).Renew(context.Background(), groupRenewIdentity(),
		SubscriptionCreateRequest{Body: domain.RequestObject{}, Group: "A1000000-0000-4000-8000-000000714001"})
	if text := renewFailureText(t, err); text != "'associate_data'" {
		t.Fatalf("unexpected text: %q", text)
	}
	if tx.group == nil || *tx.group != "a1000000-0000-4000-8000-000000714001" {
		t.Fatalf("group = %v", tx.group)
	}
	if tx.rollbacks != 1 {
		t.Fatalf("transaction rolled back %d times", tx.rollbacks)
	}
}

func TestRenewKeepsAMalformedGroupUnfiltered(t *testing.T) {
	tx := &groupRenewTransaction{associationCount: 1}
	_, err := newGroupRenewals(tx).Renew(context.Background(), groupRenewIdentity(),
		SubscriptionCreateRequest{Body: domain.RequestObject{}, Group: " zzz "})
	if text := renewFailureText(t, err); text != "'associate_data'" {
		t.Fatalf("unexpected text: %q", text)
	}
	if tx.group != nil {
		t.Fatalf("group = %v, want no filter", *tx.group)
	}
	if tx.rollbacks != 1 {
		t.Fatalf("transaction rolled back %d times", tx.rollbacks)
	}
}

func TestApproveAcceptsAGroupHeader(t *testing.T) {
	approvals := NewSubscriptionDraftApprovals(nil, nil)
	_, err := approvals.Approve(context.Background(), groupRenewIdentity(),
		SubscriptionCreateRequest{Body: domain.RequestObject{}, Group: "zzz"})
	if !errors.Is(err, ErrDraftApproveMissingIDs) {
		t.Fatalf("want the missing-ids refusal, got %v", err)
	}
}

func TestDraftApproveSwallowsADatabaseError(t *testing.T) {
	if !draftPythonError(&pgconn.PgError{Code: "23505", Message: "duplicate key value"}) {
		t.Fatal("a draft's database error is swallowed by the view")
	}
	if draftPythonError(errors.New("unexpected")) {
		t.Fatal("an unrelated error is not a swallowed one")
	}
}
