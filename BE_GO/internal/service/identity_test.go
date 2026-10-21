package service

import (
	"context"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type identityRelationsFailure struct {
	at      string
	failure error
}

func (r identityRelationsFailure) AssociationOwnedBy(context.Context, string) (string, error) {
	if r.at == "association" {
		return "", r.failure
	}
	return "00000000-0000-4000-8000-000000000080", nil
}
func (r identityRelationsFailure) EligibleTarget(context.Context, string, string, string) (domain.Account, error) {
	return domain.Account{}, r.failure
}
func (r identityRelationsFailure) PrimaryExportOwner(context.Context) (domain.Account, error) {
	return domain.Account{}, r.failure
}

type identitySessionFailure struct {
	record domain.ImpersonationSession
	err    error
	calls  int
}

func (r *identitySessionFailure) Get(context.Context, string) (domain.ImpersonationSession, error) {
	r.calls++
	return r.record, r.err
}

func TestIdentityResolutionPropagatesInfrastructureFailures(t *testing.T) {
	failure := errors.New("provider unavailable")
	owner := domain.Account{ID: "00000000-0000-4000-8000-000000000008", Role: 1, Active: true}
	for _, stage := range []string{"account", "association", "session", "target", "primary-export"} {
		t.Run(stage, func(t *testing.T) {
			accounts := &actorAccounts{account: owner}
			relations := identityRelationsFailure{at: stage, failure: failure}
			sessions := &identitySessionFailure{record: domain.ImpersonationSession{ActorID: owner.ID, TargetID: "00000000-0000-4000-8000-000000000010", AssociationID: "00000000-0000-4000-8000-000000000080"}}
			if stage == "account" {
				accounts.err = failure
			}
			if stage == "session" {
				sessions.err = failure
			}
			actor := owner
			request := domain.IdentityRequest{Path: "/profile/info", Method: "GET", SessionID: "00000000-0000-4000-8000-000000000100"}
			if stage == "primary-export" {
				actor.Superuser = true
				request.SessionID = ""
				request.Path = "/association/export/all"
			}
			identity, err := NewIdentityResolver(accounts, relations, sessions, NewPermissions()).Resolve(context.Background(), &actor, request)
			if !errors.Is(err, failure) || identity.Actor != nil || identity.Effective != nil || identity.ScopeUser != nil {
				t.Fatal("infrastructure failure became a usable identity or permission decision")
			}
		})
	}
}

func TestIdentityResolutionCancellationRunsNoLookups(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	accounts := &actorAccounts{}
	sessions := &identitySessionFailure{}
	_, err := NewIdentityResolver(accounts, identityRelationsFailure{}, sessions, NewPermissions()).Resolve(ctx, nil, domain.IdentityRequest{SessionID: "anything"})
	if !errors.Is(err, context.Canceled) || accounts.calls != 0 || sessions.calls != 0 {
		t.Fatal("cancelled identity resolution continued")
	}
}
