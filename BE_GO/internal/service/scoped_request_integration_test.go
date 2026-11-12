//go:build integration

package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

type countedGuardDB struct {
	*sqlx.DB
	queries atomic.Int64
}

func (db *countedGuardDB) QueryxContext(ctx context.Context, query string, args ...any) (*sqlx.Rows, error) {
	db.queries.Add(1)
	return db.DB.QueryxContext(ctx, query, args...)
}

func TestScopedRequestWithDjangoResources(t *testing.T) {
	data, err := os.ReadFile(os.Getenv("ASSOZETA_SCOPED_REQUESTS_FIXTURE"))
	if err != nil {
		t.Fatal("run through TESTS/adapters/django/accounts.py")
	}
	var cases []struct {
		Name, Operation, Method, Actor, Association, Session, Group, Detail, Initiator, Route string
		Resolve                                                                               bool
		Role                                                                                  int
		Body, Path, Query                                                                     json.RawMessage
	}
	if err := json.Unmarshal(data, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases) < 70 {
		t.Fatal("missing scoped request contracts")
	}
	db, err := sqlx.Connect("pgx", os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal("private database unavailable")
	}
	defer db.Close()
	db.SetMaxOpenConns(4)
	cache := redis.NewClient(&redis.Options{Addr: os.Getenv("ASSOZETA_IDENTITY_CACHE"), DB: 1, Password: "synthetic-identity-only"})
	defer cache.Close()
	uploads, err := repository.NewDjangoImpersonationCache(cache)
	if err != nil {
		t.Fatal(err)
	}
	guard := NewScopedRequestGuard(repository.NewScopedResources(db), repository.NewIdentityRelations(db), uploads)
	for _, tc := range cases {
		t.Run(tc.Name, func(t *testing.T) {
			parse := func(data json.RawMessage) any {
				t.Helper()
				value, err := dto.ParseReferenceJSON(data)
				if err != nil {
					t.Fatal(err)
				}
				return value
			}
			request := domain.IdentityRequest{Method: tc.Method, SessionID: tc.Session, GroupID: tc.Group,
				References: &domain.RequestReferences{Operation: tc.Operation, Path: parse(tc.Path).(domain.RequestObject), Query: parse(tc.Query).(domain.RequestObject), Body: parse(tc.Body)}}
			identity := domain.ResolvedIdentity{Effective: &domain.Account{ID: tc.Actor, Role: tc.Role}, ImpersonationAssociationID: tc.Association, SessionID: tc.Session}
			var err error
			if tc.Resolve {
				accounts := repository.NewAccounts(db)
				actor, readErr := accounts.ByID(context.Background(), tc.Initiator)
				if readErr != nil {
					t.Fatal(readErr)
				}
				request.Path = tc.Route
				resolver := NewIdentityResolver(accounts, repository.NewIdentityRelations(db), uploads, NewPermissions(), guard)
				resolved, resolveErr := resolver.Resolve(context.Background(), &actor, request)
				err = resolveErr
				if err != nil && (resolved.Actor != nil || resolved.Effective != nil || resolved.ScopeUser != nil) {
					t.Fatal("failed admission leaked an identity")
				}
				if err == nil && (resolved.Effective == nil || resolved.Effective.ID != tc.Actor) {
					t.Fatal("admitted wrong effective identity")
				}
			} else {
				err = guard.Check(context.Background(), identity, request)
			}
			if tc.Detail == "" {
				if err != nil {
					t.Fatal(err)
				}
				return
			}
			var denied *domain.PermissionDenied
			if !errors.As(err, &denied) || denied.Detail != tc.Detail || denied.ImpersonationInvalid {
				t.Fatalf("denial = %v; want %s without invalid-session flag", err, tc.Detail)
			}
		})
	}
	counted := &countedGuardDB{DB: db}
	guard = NewScopedRequestGuard(repository.NewScopedResources(counted), repository.NewIdentityRelations(db), uploads)
	identity := domain.ResolvedIdentity{Effective: &domain.Account{ID: guardFixtureID(30), Role: 2}, ImpersonationAssociationID: guardFixtureID(80), SessionID: guardFixtureID(12000)}
	makeRequest := func(size int) domain.IdentityRequest {
		body := make([]any, 0, size)
		for i := 0; i < size; i++ {
			body = append(body, domain.RequestObject{{Name: "tag_id", Value: guardFixtureID(10000 + i)}, {Name: "course_id", Value: guardFixtureID(5010)}})
		}
		return domain.IdentityRequest{Method: "POST", References: &domain.RequestReferences{Operation: "profile_info", Body: body}}
	}
	for _, size := range []int{1, 16, 128, 512, 1025} {
		before := counted.queries.Load()
		if err := guard.Check(context.Background(), identity, makeRequest(size)); err != nil {
			t.Fatal(err)
		}
		want := int64((size+511)/512 + 1) // distinct tag batches and one repeated course
		if got := counted.queries.Load() - before; got != want {
			t.Fatalf("%d alternating references: %d SQL queries, want %d", size, got, want)
		}
	}
	var workers sync.WaitGroup
	for i := 0; i < 32; i++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			if err := guard.Check(context.Background(), identity, makeRequest(16)); err != nil {
				t.Errorf("concurrent admission: %v", err)
			}
		}()
	}
	workers.Wait()
	if db.Stats().InUse != 0 {
		t.Fatal("guard retained pooled connections")
	}
	// The linked-resource lookup does not grant a new upload by itself. Observe
	// Redis revocation on the same guard instance; never cache prior admission.
	medicalRequest := domain.IdentityRequest{Method: "POST", References: &domain.RequestReferences{Operation: "profile_info", Body: domain.RequestObject{{Name: "medical_id", Value: guardFixtureID(1104)}}}}
	key := ":1:impersonation-upload:" + identity.SessionID + ":" + guardFixtureID(1104)
	rawGrant, err := cache.Get(context.Background(), key).Bytes()
	if err != nil {
		t.Fatal(err)
	}
	if err := cache.Expire(context.Background(), key, 5*time.Second).Err(); err != nil {
		t.Fatal(err)
	}
	if err := guard.Check(context.Background(), identity, medicalRequest); err != nil {
		t.Fatal(err)
	}
	ttl, err := cache.TTL(context.Background(), key).Result()
	if err != nil || ttl <= 0 || ttl > 5*time.Second {
		t.Fatal("upload grant lost or renewed TTL")
	}
	if err := cache.Del(context.Background(), key).Err(); err != nil {
		t.Fatal(err)
	}
	var denied *domain.PermissionDenied
	if err := guard.Check(context.Background(), identity, medicalRequest); !errors.As(err, &denied) || denied.Detail != unavailableMedical {
		t.Fatal("revoked upload grant survived admission")
	}
	if err := cache.Set(context.Background(), key, rawGrant, 5*time.Millisecond).Err(); err != nil {
		t.Fatal(err)
	}
	time.Sleep(10 * time.Millisecond)
	if err := guard.Check(context.Background(), identity, medicalRequest); !errors.As(err, &denied) || denied.Detail != unavailableMedical {
		t.Fatal("expired upload grant survived admission")
	}
	// A cache failure must not become admission even for an otherwise linked
	// certificate: Django reads the upload cache before evaluating SQL existence.
	if err := cache.Close(); err != nil {
		t.Fatal(err)
	}
	before := counted.queries.Load()
	medicalRequest.References.Body = domain.RequestObject{{Name: "medical_id", Value: guardFixtureID(1101)}}
	if err := guard.Check(context.Background(), identity, medicalRequest); err == nil || errors.As(err, &denied) {
		t.Fatal("cache failure became a permission decision")
	}
	if counted.queries.Load() != before {
		t.Fatal("medical SQL ran after cache failure")
	}
}

func guardFixtureID(n int) string { return fmt.Sprintf("00000000-0000-4000-8000-%012d", n) }
