//go:build integration

package repository

import (
	"context"
	"errors"
	"os"
	"reflect"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

const ownerID = "00000000-0000-4000-8000-000000000001"
const collaboratorID = "00000000-0000-4000-8000-000000000002"
const disabledID = "00000000-0000-4000-8000-000000000003"
const deletedID = "00000000-0000-4000-8000-000000000004"

func accountDatabase(t *testing.T) *sqlx.DB {
	t.Helper()
	dsn := os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN")
	if dsn == "" {
		t.Fatal("run through TESTS/adapters/django/accounts.py; no skipped database checks")
	}
	db, err := sqlx.Connect("pgx", dsn)
	if err != nil {
		t.Fatal("cannot connect to disposable account database")
	}
	t.Cleanup(func() { db.Close() })
	return db
}

func TestAccountReadsExistingDjangoRows(t *testing.T) {
	repo := NewAccounts(accountDatabase(t))
	ctx := context.Background()
	owner, err := repo.ByID(ctx, ownerID)
	if err != nil {
		t.Fatal(err)
	}
	if owner.ID != ownerID || owner.Username != "Owner" || owner.Role != 1 || !owner.Active || !owner.Superuser || owner.ConnectedUserID != nil || string(owner.Permissions) != "null" {
		t.Fatal("Django owner identity or nullable fields changed")
	}
	collaborator, err := repo.ByID(ctx, collaboratorID)
	if err != nil {
		t.Fatal(err)
	}
	if collaborator.Role != 3 || collaborator.CollaboratorRole != 3 || collaborator.ConnectedUserID == nil || *collaborator.ConnectedUserID != ownerID || string(collaborator.Permissions) != `["association.courses.read"]` {
		t.Fatal("collaborator's own role/grants/owner must remain separate")
	}
	if collaborator.Password != "!synthetic-unusable" {
		t.Fatal("password encoding changed")
	}
	disabled, err := repo.ByID(ctx, disabledID)
	if err != nil || disabled.Active {
		t.Fatal("inactive account must remain distinguishable for authentication rejection")
	}
	for _, id := range []string{deletedID, "00000000-0000-4000-8000-999999999999"} {
		if _, err := repo.ByID(ctx, id); !errors.Is(err, domain.ErrAccountNotFound) {
			t.Fatalf("missing/deleted account: %v", err)
		}
	}
}

func TestLoginCandidateLookupPreservesDuplicatesAndLiteralMatching(t *testing.T) {
	repo := NewAccounts(accountDatabase(t))
	cases := []struct {
		name       string
		byEmail    bool
		identifier string
		ids        []string
	}{
		{"email duplicates include disabled but not deleted", true, "SHARED@example.test", []string{ownerID, collaboratorID, disabledID}},
		{"case-insensitive username", false, "oWnEr", []string{ownerID}},
		{"literal SQL wildcard characters", true, "literal%_@example.test", []string{"00000000-0000-4000-8000-000000000005"}},
		{"SQL injection stays data", true, "' OR 1=1 --", nil},
		{"unknown username", false, "nobody", nil},
		{"case-folded duplicate usernames", false, "DuP", []string{"00000000-0000-4000-8000-000000000006", "00000000-0000-4000-8000-000000000007"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var ids []string
			err := repo.VisitLoginCandidates(context.Background(), tc.byEmail, tc.identifier, func(account domain.Account) error { ids = append(ids, account.ID); return nil })
			if err != nil {
				t.Fatal(err)
			}
			if !reflect.DeepEqual(ids, tc.ids) {
				t.Fatalf("candidate IDs = %v; want %v", ids, tc.ids)
			}
		})
	}
}

func TestAccountReadsHonorCancellationAndVisitorFailure(t *testing.T) {
	repo := NewAccounts(accountDatabase(t))
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := repo.ByID(ctx, ownerID); !errors.Is(err, context.Canceled) {
		t.Fatalf("ByID cancellation: %v", err)
	}
	if err := repo.VisitLoginCandidates(ctx, true, "shared@example.test", func(domain.Account) error { t.Fatal("visitor ran after cancellation"); return nil }); !errors.Is(err, context.Canceled) {
		t.Fatalf("candidate cancellation: %v", err)
	}
	failure := errors.New("credential verifier unavailable")
	calls := 0
	err := repo.VisitLoginCandidates(context.Background(), true, "shared@example.test", func(domain.Account) error { calls++; return failure })
	if calls != 1 || !errors.Is(err, failure) {
		t.Fatalf("visitor failure not propagated: %v", err)
	}
	ctx, cancel = context.WithCancel(context.Background())
	defer cancel()
	calls = 0
	err = repo.VisitLoginCandidates(ctx, true, "shared@example.test", func(domain.Account) error { calls++; cancel(); return nil })
	if calls != 1 || !errors.Is(err, context.Canceled) {
		t.Fatalf("mid-stream cancellation: calls=%d err=%v", calls, err)
	}
}
