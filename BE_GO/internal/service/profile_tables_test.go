package service

import (
	"context"
	"encoding/json"
	"errors"
	"reflect"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type tableStoreTest struct {
	tx     tableTransactionTest
	begins int
	err    error
}

func (s *tableStoreTest) BeginProfileTables(context.Context) (domain.ProfileTablesTransaction, error) {
	s.begins++
	return &s.tx, s.err
}

type tableTransactionTest struct {
	profileUpdateTxTest
	row    domain.ProfileTablePreferences
	target string
	value  json.RawMessage
	writes int
}

func (t *tableTransactionTest) TablePreferencesForUpdate(_ context.Context, id string) (domain.ProfileTablePreferences, error) {
	t.target = id
	return t.row, t.check("read")
}
func (t *tableTransactionTest) SetTablePreferences(_ context.Context, id string, value json.RawMessage) error {
	t.target = id
	t.value = value
	t.writes++
	return t.check("write")
}
func tablesFixture() (*tableStoreTest, domain.ResolvedIdentity, dto.ProfileTablesRequest) {
	owner := &domain.Account{ID: "owner", Role: 1, Email: "owner@example.test"}
	actor := &domain.Account{ID: "collaborator", Role: 3, Email: "collaborator@example.test", ConnectedUserID: &owner.ID}
	return &tableStoreTest{tx: tableTransactionTest{profileUpdateTxTest: profileUpdateTxTest{associations: map[string]string{"owner": "association"}}, row: domain.ProfileTablePreferences{ID: "owner", FirstName: "Owner", LastName: "Fixture", Tables: json.RawMessage(`{"before":[1]}`)}}}, domain.ResolvedIdentity{Actor: actor, Effective: actor, ScopeUser: owner, Collaborator: true}, dto.ProfileTablesRequest{Present: true, Value: domain.RequestObject{{Name: "columns", Value: []any{"è", true}}}}
}
func TestProfileTablesOwnerScopeAndAudit(t *testing.T) {
	store, id, input := tablesFixture()
	now := time.Unix(123, 0)
	err := NewProfileTables(store, func() time.Time { return now }).Update(context.Background(), id, input, domain.LoginAuditContext{ActorID: &id.ScopeUser.ID, AdditionalData: &domain.AuditImpersonation{SessionID: "forged"}})
	if err != nil || !store.tx.committed || store.tx.target != "owner" || string(store.tx.value) != `{"columns": ["\u00e8", true]}` {
		t.Fatal(err, store.tx)
	}
	if len(store.tx.audits) != 1 {
		t.Fatal(store.tx.audits)
	}
	event := store.tx.audits[0]
	if event.Model != "user" || event.ID != "owner" || event.ObjectRepresentation != "name: Owner Fixture" || event.At != now || !reflect.DeepEqual(event.Changes, map[string][2]string{"tables_settings": {`{"before": [1]}`, `{"columns": ["\u00e8", true]}`}}) {
		t.Fatal(event)
	}
	if *event.Context.ActorID != "collaborator" || *event.Context.ActorEmail != "collaborator@example.test" || event.Context.AdditionalData != nil || *event.AssociationID != "association" || *event.ResolutionPath != "user.sport_association" {
		t.Fatal(event)
	}
}
func TestProfileTablesFailureBoundaries(t *testing.T) {
	for _, step := range []string{"begin", "read", "write", "associations", "audit", "commit", "foreign-row", "foreign-owner", "cancelled", "no-field", "unchanged", "retries", "exhausted"} {
		t.Run(step, func(t *testing.T) {
			store, id, input := tablesFixture()
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			want := errProfileTest
			begins, rollbacks := 1, 1
			store.tx.fail = step
			switch step {
			case "begin":
				store.err = errProfileTest
				rollbacks = 0
			case "foreign-row":
				store.tx.row.ID = "foreign"
				want = domain.ErrInvalidScopedRequest
			case "foreign-owner":
				id.ScopeUser = &domain.Account{ID: "foreign", Role: 1}
				want = domain.ErrInvalidScopedRequest
				begins, rollbacks = 0, 0
			case "cancelled":
				cancel()
				want = context.Canceled
				begins, rollbacks = 0, 0
			case "no-field":
				input.Present = false
				want = nil
				begins, rollbacks = 0, 0
			case "unchanged":
				input.Value = domain.RequestObject{{Name: "before", Value: []any{json.Number("1")}}}
				want = nil
			case "retries":
				store.tx.conflicts = 2
				begins, rollbacks = 3, 3
				want = nil
			case "exhausted":
				store.tx.conflicts = 4
				begins, rollbacks = 3, 3
				want = domain.ErrCredentialConflict
			}
			err := NewProfileTables(store, time.Now).Update(ctx, id, input, domain.LoginAuditContext{})
			if !errors.Is(err, want) || store.begins != begins || store.tx.rollbacks != rollbacks || store.tx.committed != (want == nil && step != "no-field") {
				t.Fatal(err, store.begins, store.tx)
			}
			if (step == "no-field" || step == "unchanged") && len(store.tx.audits) != 0 {
				t.Fatal("spurious audit", store.tx.audits)
			}
		})
	}
}
