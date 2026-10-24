package service

import (
	"context"
	"encoding/json"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestScopedReferenceNumbersBoundExponentAndIntegerWork(t *testing.T) {
	if athleteRoleValue(json.Number("2e1000000000")) || !athleteRoleValue(json.Number("2.00000000000000001")) {
		t.Fatal("Python role number semantics changed")
	}
	if truthyReference(json.Number("1e-1000000000")) || !truthyReference(json.Number("1e1000000000")) {
		t.Fatal("Python number truthiness changed")
	}
	if _, _, err := referenceUUID(json.Number("3402823669209384634633746074317682114550")); !errors.Is(err, domain.ErrInvalidResourceBatch) {
		t.Fatal("oversized integer reference accepted")
	}
}

type failingScopeProvider struct {
	err   error
	calls int
}

func (p *failingScopeProvider) Available(context.Context, domain.ResourceScope, string, []string) ([]string, error) {
	p.calls++
	return nil, p.err
}
func (p *failingScopeProvider) AssociationUsername(context.Context, string) (string, error) {
	p.calls++
	return "", p.err
}
func (p *failingScopeProvider) MedicalUploads(context.Context, string, []string) (map[string]string, error) {
	p.calls++
	return nil, p.err
}

func TestScopedRequestPropagatesFailuresAndRequiresRouteMetadata(t *testing.T) {
	failure := errors.New("dependency unavailable")
	provider := &failingScopeProvider{err: failure}
	guard := NewScopedRequestGuard(provider, provider, provider)
	identity := domain.ResolvedIdentity{Effective: &domain.Account{ID: "00000000-0000-4000-8000-000000000030", Role: 2}, ImpersonationAssociationID: "00000000-0000-4000-8000-000000000080"}
	for _, input := range []domain.RequestReferences{
		{Operation: "profile_info", Body: domain.RequestObject{{Name: "course", Value: "00000000-0000-4000-8000-000000005010"}}},
		{Operation: "profile_info", Body: domain.RequestObject{{Name: "medical", Value: "00000000-0000-4000-8000-000000001101"}}},
		{Operation: "search_profile"},
	} {
		if err := guard.Check(context.Background(), identity, domain.IdentityRequest{Method: "POST", References: &input}); !errors.Is(err, failure) {
			t.Fatalf("provider failure became permission success/denial: %v", err)
		}
	}
	before := provider.calls
	if err := guard.Check(context.Background(), identity, domain.IdentityRequest{}); !errors.Is(err, domain.ErrInvalidScopedRequest) {
		t.Fatal("missing trusted route metadata accepted")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := guard.Check(ctx, identity, domain.IdentityRequest{}); !errors.Is(err, context.Canceled) {
		t.Fatal("cancelled guard continued")
	}
	if provider.calls != before {
		t.Fatal("invalid/cancelled admission contacted a dependency")
	}
}

type identityTargetRelations struct {
	identityRelationsFailure
	target domain.Account
}

func (r identityTargetRelations) EligibleTarget(context.Context, string, string, string) (domain.Account, error) {
	return r.target, nil
}

type inspectScopeGuard struct {
	t       *testing.T
	failure error
}

func (g inspectScopeGuard) Check(_ context.Context, identity domain.ResolvedIdentity, _ domain.IdentityRequest) error {
	if identity.Collaborator || identity.ScopeUser.ID != identity.Effective.ID {
		g.t.Fatal("collaborator mapping ran before scoped admission")
	}
	return g.failure
}
func TestIdentityScopedFailurePrecedesCollaboratorMappingAndFailsClosed(t *testing.T) {
	owner := domain.Account{ID: "00000000-0000-4000-8000-000000000008", Role: 1, Active: true}
	target := domain.Account{ID: "00000000-0000-4000-8000-000000000011", Role: 3, Active: true} // missing owner would be a later denial
	accounts := &actorAccounts{account: owner}
	relations := identityTargetRelations{target: target}
	sessions := &identitySessionFailure{record: domain.ImpersonationSession{ActorID: owner.ID, TargetID: target.ID, AssociationID: "00000000-0000-4000-8000-000000000080"}}
	failure := errors.New("guard dependency unavailable")
	for _, guard := range []ScopedRequestChecker{nil, inspectScopeGuard{t: t, failure: failure}} {
		resolver := NewIdentityResolver(accounts, relations, sessions, NewPermissions(), guard)
		result, err := resolver.Resolve(context.Background(), &owner, domain.IdentityRequest{Path: "/course/add", SessionID: "00000000-0000-4000-8000-000000012000"})
		want := failure
		if guard == nil {
			want = domain.ErrInvalidScopedRequest
		}
		if !errors.Is(err, want) || result.Actor != nil || result.Effective != nil || result.ScopeUser != nil {
			t.Fatalf("guard failure leaked identity or changed ordering: %v", err)
		}
	}
}
