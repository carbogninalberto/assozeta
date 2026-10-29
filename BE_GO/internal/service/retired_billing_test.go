package service

import (
	"context"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestRetiredBillingUsesAdmittedScopeRole(t *testing.T) {
	flow := NewRetiredSoftwareBilling()
	actor := &domain.Account{ID: "actor", Role: domain.RoleCollaborator, Active: true}
	for _, role := range []int{domain.RoleAssociation, domain.RoleAthlete, domain.RoleCollaborator, 0, 99} {
		scope := &domain.Account{ID: "scope", Role: role, Active: true}
		allowed, err := flow.AssociationTombstone(context.Background(), domain.ResolvedIdentity{Actor: actor, Effective: actor, ScopeUser: scope})
		if err != nil || allowed != (role == domain.RoleAssociation) {
			t.Fatal("retired billing used actor role or granted unsupported scope", err)
		}
	}
	owner := &domain.Account{ID: "owner", Role: domain.RoleAssociation, Active: true}
	athlete := &domain.Account{ID: "athlete", Role: domain.RoleAthlete, Active: true}
	if allowed, err := flow.AssociationTombstone(context.Background(), domain.ResolvedIdentity{Actor: owner, Effective: athlete, ScopeUser: athlete}); err != nil || allowed {
		t.Fatal("owner role leaked into selected athlete scope", err)
	}
}

func TestRetiredBillingRequiresIdentityAndHonorsCancellation(t *testing.T) {
	flow := NewRetiredSoftwareBilling()
	account := &domain.Account{ID: "owner", Role: domain.RoleAssociation, Active: true}
	for _, identity := range []domain.ResolvedIdentity{{}, {Actor: account}, {Actor: account, Effective: account}, {Effective: account, ScopeUser: account}} {
		if _, err := flow.AssociationTombstone(context.Background(), identity); err == nil {
			t.Fatal("missing identity became a tombstone result")
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := flow.AssociationTombstone(ctx, domain.ResolvedIdentity{Actor: account, Effective: account, ScopeUser: account}); !errors.Is(err, context.Canceled) {
		t.Fatal("retired billing ignored cancellation", err)
	}
}
