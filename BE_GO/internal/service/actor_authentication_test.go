package service

import (
	"context"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type actorAccounts struct {
	account domain.Account
	err     error
	calls   int
	id      string
}

func (r *actorAccounts) ByID(ctx context.Context, id string) (domain.Account, error) {
	r.calls++
	r.id = id
	return r.account, r.err
}

type actorTokens struct {
	claims map[string]any
	err    error
	calls  int
}

func (v *actorTokens) Verify(encoded, kind string) (map[string]any, error) {
	v.calls++
	if encoded != "credential" || kind != "access" {
		return nil, errors.New("unexpected token use")
	}
	return v.claims, v.err
}

func TestActorAuthenticationTrustsCurrentAccountNotClaims(t *testing.T) {
	id := "00000000-0000-4000-8000-000000000001"
	accounts := &actorAccounts{account: domain.Account{ID: id, Role: 3, Active: true, Email: "stored@example.test", Password: "sensitive-hash"}}
	tokens := &actorTokens{claims: map[string]any{"user_id": id, "role": 1, "email": "forged@example.test", "is_superuser": true}}
	auth := NewActorAuthentication(accounts, tokens)
	actor, err := auth.Authenticate(context.Background(), "Bearer credential")
	if err != nil {
		t.Fatal(err)
	}
	if actor.Role != 3 || actor.Superuser || actor.Email != "stored@example.test" || actor.Password != "" || accounts.id != id {
		t.Fatal("claims replaced persisted identity or password escaped account lookup")
	}
	accounts.account.Active = false
	if _, err := auth.Authenticate(context.Background(), "Bearer credential"); authCode(err) != "user_inactive" {
		t.Fatalf("did not observe deactivation: %v", err)
	}
	accounts.err = domain.ErrAccountNotFound
	if _, err := auth.Authenticate(context.Background(), "Bearer credential"); authCode(err) != "user_not_found" {
		t.Fatalf("did not observe deletion: %v", err)
	}
	if accounts.calls != 3 {
		t.Fatal("account state was cached")
	}
}
func authCode(err error) string {
	var failure *domain.AuthenticationFailure
	if errors.As(err, &failure) {
		return failure.Code
	}
	return ""
}
func TestActorAuthenticationHeaderAndFailureAdmission(t *testing.T) {
	for _, header := range []string{"", " ", "Basic credential", "bearer credential"} {
		accounts := &actorAccounts{}
		tokens := &actorTokens{}
		actor, err := NewActorAuthentication(accounts, tokens).Authenticate(context.Background(), header)
		if actor != nil || err != nil || accounts.calls != 0 || tokens.calls != 0 {
			t.Fatalf("unexpected Bearer admission: %q", header)
		}
	}
	for _, header := range []string{"Bearer", "Bearer credential extra"} {
		accounts := &actorAccounts{}
		tokens := &actorTokens{}
		if _, err := NewActorAuthentication(accounts, tokens).Authenticate(context.Background(), header); authCode(err) != "bad_authorization_header" || accounts.calls != 0 || tokens.calls != 0 {
			t.Fatalf("bad header reached credential lookup: %q", header)
		}
	}
	for _, claims := range []map[string]any{nil, {}} {
		accounts := &actorAccounts{}
		tokens := &actorTokens{claims: claims}
		if _, err := NewActorAuthentication(accounts, tokens).Authenticate(context.Background(), "Bearer credential"); authCode(err) != "token_not_valid" || accounts.calls != 0 {
			t.Fatal("invalid identity reached account lookup")
		}
	}
	invalidAccounts := &actorAccounts{}
	invalidTokens := &actorTokens{claims: map[string]any{"user_id": "invalid"}}
	if _, err := NewActorAuthentication(invalidAccounts, invalidTokens).Authenticate(context.Background(), "Bearer credential"); !errors.Is(err, domain.ErrMalformedAccountClaim) || invalidAccounts.calls != 0 {
		t.Fatal("malformed signed identity changed failure category or reached database")
	}
	accounts := &actorAccounts{}
	tokens := &actorTokens{err: errors.New("bad signature")}
	if _, err := NewActorAuthentication(accounts, tokens).Authenticate(context.Background(), "Bearer credential"); authCode(err) != "token_not_valid" || accounts.calls != 0 {
		t.Fatal("invalid token reached account lookup")
	}
}
func TestActorAuthenticationDoesNotTurnInfrastructureFailuresIntoAnonymous(t *testing.T) {
	failure := errors.New("private database failure")
	accounts := &actorAccounts{err: failure}
	tokens := &actorTokens{claims: map[string]any{"user_id": "00000000-0000-4000-8000-000000000001"}}
	auth := NewActorAuthentication(accounts, tokens)
	if actor, err := auth.Authenticate(context.Background(), "Bearer credential"); actor != nil || !errors.Is(err, failure) {
		t.Fatal("database failure was swallowed")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	before := tokens.calls
	if _, err := auth.Authenticate(ctx, "Bearer credential"); !errors.Is(err, context.Canceled) || tokens.calls != before {
		t.Fatal("cancelled authentication continued")
	}
}
