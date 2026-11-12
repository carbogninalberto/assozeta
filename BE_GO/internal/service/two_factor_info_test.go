package service

import (
	"context"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestTwoFactorInfoUsesTrustedScopeSnapshot(t *testing.T) {
	actor := &domain.Account{ID: "actor", Active: true, TwoFactor: false}
	owner := &domain.Account{ID: "owner", Active: true, TwoFactor: true}
	flow := NewTwoFactorInfo()
	for _, identity := range []domain.ResolvedIdentity{
		{Actor: owner, Effective: owner, ScopeUser: owner},
		{Actor: actor, Effective: actor, ScopeUser: owner, Collaborator: true},
		{Actor: actor, Effective: owner, ScopeUser: owner, Impersonating: true},
	} {
		enabled, err := flow.Status(context.Background(), identity)
		if err != nil || !enabled {
			t.Fatal("status must come from admitted scope, not initiating actor", err)
		}
	}
	enabled, err := flow.Status(context.Background(), domain.ResolvedIdentity{Actor: owner, Effective: actor, ScopeUser: actor, Impersonating: true})
	if err != nil || enabled {
		t.Fatal("actor state leaked into selected scope", err)
	}
	if actor.TwoFactor || !owner.TwoFactor {
		t.Fatal("read changed identity snapshot")
	}
}

func TestTwoFactorInfoRejectsMissingIdentityAndCancellation(t *testing.T) {
	flow := NewTwoFactorInfo()
	account := &domain.Account{ID: "user", Active: true}
	for _, identity := range []domain.ResolvedIdentity{
		{}, {Actor: account}, {Actor: account, Effective: account},
		{Effective: account, ScopeUser: account},
	} {
		if _, err := flow.Status(context.Background(), identity); err == nil {
			t.Fatal("incomplete trusted identity became a status")
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := flow.Status(ctx, domain.ResolvedIdentity{Actor: account, Effective: account, ScopeUser: account}); !errors.Is(err, context.Canceled) {
		t.Fatal("status ignored caller cancellation", err)
	}
}
