//go:build integration

package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sync"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

func TestIdentityResolutionWithDjangoDatabaseAndSessions(t *testing.T) {
	path := os.Getenv("ASSOZETA_IDENTITY_FIXTURE")
	if path == "" {
		t.Fatal("run through TESTS/adapters/django/accounts.py")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Name          string `json:"name"`
		ActorID       string `json:"actor"`
		Path          string `json:"path"`
		Method        string `json:"method"`
		SessionID     string `json:"session"`
		TargetID      string `json:"target"`
		Detail        string `json:"detail"`
		Invalid       bool   `json:"invalid"`
		Effective     string `json:"effective"`
		Scope         string `json:"scope"`
		Association   string `json:"association"`
		Collaborator  bool   `json:"collaborator"`
		Impersonating bool   `json:"impersonating"`
	}
	if err := json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) == 0 {
		t.Fatal("no identity contracts")
	}
	db, err := sqlx.Connect("pgx", os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal("private database unavailable")
	}
	defer db.Close()
	cache := redis.NewClient(&redis.Options{Addr: os.Getenv("ASSOZETA_IDENTITY_CACHE"), DB: 1, Password: "synthetic-identity-only", ContextTimeoutEnabled: true})
	defer cache.Close()
	sessions, err := repository.NewDjangoImpersonationCache(cache)
	if err != nil {
		t.Fatal(err)
	}
	accounts := repository.NewAccounts(db)
	resolver := NewIdentityResolver(accounts, repository.NewIdentityRelations(db), sessions, NewPermissions())
	for _, tc := range cases {
		t.Run(tc.Name, func(t *testing.T) {
			var actor *domain.Account
			if tc.ActorID != "" {
				account, err := accounts.ByID(context.Background(), tc.ActorID)
				if err != nil {
					t.Fatal(err)
				}
				actor = &account
			}
			identity, err := resolver.Resolve(context.Background(), actor, domain.IdentityRequest{Path: tc.Path, Method: tc.Method, SessionID: tc.SessionID, TargetID: tc.TargetID})
			if tc.Detail != "" {
				var denied *domain.PermissionDenied
				if !errors.As(err, &denied) || denied.Detail != tc.Detail || denied.ImpersonationInvalid != tc.Invalid {
					t.Fatalf("denial mismatch: %v", err)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			id := func(account *domain.Account) string {
				if account == nil {
					return ""
				}
				if account.Password != "" {
					t.Error("identity leaked stored password")
				}
				return account.ID
			}
			if id(identity.Actor) != tc.ActorID || id(identity.Effective) != tc.Effective || id(identity.ScopeUser) != tc.Scope || identity.Collaborator != tc.Collaborator || identity.Impersonating != tc.Impersonating || identity.ImpersonationAssociationID != tc.Association {
				t.Fatal("actor/effective/scope identity changed")
			}
			if identity.Impersonating && identity.SessionID != tc.SessionID {
				t.Fatal("audit attribution lost session")
			}
		})
	}
	// Persisted changes are confined to this disposable fixture and restored
	// before Django's unchanged-row assertion. No production mutation API is used.
	var memberSession string
	for _, tc := range cases {
		if tc.Name == "owner-impersonates-member" {
			memberSession = tc.SessionID
		}
	}
	if memberSession == "" {
		t.Fatal("missing transition session")
	}
	actor, err := accounts.ByID(context.Background(), "00000000-0000-4000-8000-000000000008")
	if err != nil {
		t.Fatal(err)
	}
	request := domain.IdentityRequest{Path: "/profile/info", Method: "GET", SessionID: memberSession}
	transitions := []struct {
		name, table, column, key, id, detail string
		changed, original                    bool
	}{
		{"actor-disabled", "bakney_user", "is_active", "user_id", "00000000-0000-4000-8000-000000000008", "Account non disponibile.", false, true},
		{"actor-deleted", "bakney_user", "deleted", "user_id", "00000000-0000-4000-8000-000000000008", "Account non disponibile.", true, false},
		{"target-disabled", "bakney_user", "is_active", "user_id", "00000000-0000-4000-8000-000000000010", "Utente non disponibile per l’impersonificazione.", false, true},
		{"target-deleted", "bakney_user", "deleted", "user_id", "00000000-0000-4000-8000-000000000010", "Utente non disponibile per l’impersonificazione.", true, false},
		{"association-deleted", "application_sportassociation", "deleted", "sport_association_id", "00000000-0000-4000-8000-000000000080", "Associazione non disponibile.", true, false},
		{"membership-removed", "application_associate", "deleted", "associate_id", "00000000-0000-4000-8000-000000000081", "Utente non disponibile per l’impersonificazione.", true, false},
	}
	for _, change := range transitions {
		t.Run(change.name, func(t *testing.T) {
			// Identifiers are constants above, never values from the fixture/request.
			query := fmt.Sprintf("UPDATE %s SET %s=$1 WHERE %s=$2", change.table, change.column, change.key)
			defer func() {
				if _, err := db.ExecContext(context.Background(), query, change.original, change.id); err != nil {
					t.Error("could not restore private fixture")
				}
			}()
			updated, err := db.ExecContext(context.Background(), query, change.changed, change.id)
			if err != nil {
				t.Fatal(err)
			}
			if rows, err := updated.RowsAffected(); err != nil || rows != 1 {
				t.Fatal("transition did not change exactly one fixture row")
			}
			_, err = resolver.Resolve(context.Background(), &actor, request)
			var denied *domain.PermissionDenied
			if !errors.As(err, &denied) || denied.Detail != change.detail || !denied.ImpersonationInvalid {
				t.Fatalf("eligibility revocation failed: %v", err)
			}
		})
	}

	admin, err := accounts.ByID(context.Background(), "00000000-0000-4000-8000-000000000001")
	if err != nil {
		t.Fatal(err)
	}
	for _, change := range []struct {
		column            string
		changed, original any
	}{
		{"self_hosted", false, true}, {"primary_association_id", nil, "00000000-0000-4000-8000-000000000080"},
	} {
		t.Run("primary-export-"+change.column, func(t *testing.T) {
			query := fmt.Sprintf("UPDATE instance_instanceconfiguration SET %s=$1 WHERE id=(SELECT id FROM instance_instanceconfiguration ORDER BY id LIMIT 1)", change.column)
			defer func() {
				if _, err := db.ExecContext(context.Background(), query, change.original); err != nil {
					t.Error("could not restore private instance fixture")
				}
			}()
			if _, err := db.ExecContext(context.Background(), query, change.changed); err != nil {
				t.Fatal(err)
			}
			_, err := resolver.Resolve(context.Background(), &admin, domain.IdentityRequest{Path: "/association/export/all", Method: "GET"})
			var denied *domain.PermissionDenied
			if !errors.As(err, &denied) || denied.ImpersonationInvalid || denied.Detail != "Nessuna associazione principale configurata per l’export." {
				t.Fatal("unavailable primary export scope admitted")
			}
		})
	}
	db.SetMaxOpenConns(4)
	concurrent, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	var readers sync.WaitGroup
	for i := 0; i < 32; i++ {
		readers.Add(1)
		go func() {
			defer readers.Done()
			identity, err := resolver.Resolve(concurrent, &actor, request)
			if err != nil || identity.Effective == nil || identity.Effective.ID != "00000000-0000-4000-8000-000000000010" {
				t.Error("concurrent identity lookup failed")
			}
		}()
	}
	readers.Wait()
	if db.Stats().InUse != 0 {
		t.Fatal("identity reads retained database connections")
	}
	if err := sessions.Delete(context.Background(), memberSession); err != nil {
		t.Fatal(err)
	}
	_, err = resolver.Resolve(context.Background(), &actor, request)
	var denied *domain.PermissionDenied
	if !errors.As(err, &denied) || !denied.ImpersonationInvalid || denied.Detail != "Sessione di impersonificazione scaduta o revocata. Torna al tuo account." {
		t.Fatal("cached identity survived session revocation")
	}

}
