package service

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

func TestProfileUpdateResponseRolesAndFlags(t *testing.T) {
	for _, role := range []int{0, 1, 2, 3} {
		for flags := 0; flags < 8; flags++ {
			u := &domain.Account{ID: "owner", Role: role}
			scope := u
			if role == domain.RoleCollaborator {
				owner := "connected-owner"
				u.ConnectedUserID = &owner
				scope = &domain.Account{ID: owner, Role: domain.RoleAssociation}
			}
			image := "raw"
			profiles := &responseProfiles{profile: &dto.UserAuthProfile{UserID: "owner", AvatarImage: &image}}
			associations := &responseAssociations{profile: &dto.AssociationProfile{ID: "association", Logo: &image}}
			flow := NewProfileUpdateResponses(profiles, associations, &responseImages{}, domain.RegistrationChoices{Family: flags&1 != 0, Federation: flags&2 != 0, Multiple: flags&4 != 0})
			result, err := flow.Read(context.Background(), domain.ResolvedIdentity{Actor: u, Effective: u, ScopeUser: scope})
			if err != nil || result.UserData.AvatarImage == nil || *result.UserData.AvatarImage != "converted:raw" {
				t.Fatal(role, flags, result, err)
			}
			wantRole := "association"
			if role == 2 {
				wantRole = "athlete"
			}
			if result.Role != wantRole {
				t.Fatal(result.Role)
			}
			encoded, err := json.Marshal(result)
			if err != nil {
				t.Fatal(err)
			}
			var body map[string]any
			if err = json.Unmarshal(encoded, &body); err != nil {
				t.Fatal(err)
			}
			if len(body) != 2 || body["role"] != wantRole {
				t.Fatal(body)
			}
			data := body["user_data"].(map[string]any)
			if _, ok := data["preview_and_custom_features"]; ok != (role == 1) {
				t.Fatal("feature field ownership", data)
			}
			if (result.UserData.SportAssociation != nil) != (role == 1 || role == 3) || associations.calls != map[bool]int{true: 1, false: 0}[role == 1 || role == 3] {
				t.Fatal("association ownership", result)
			}
			if role == 1 {
				features := data["preview_and_custom_features"].([]any)
				count := 0
				for bit := 0; bit < 3; bit++ {
					if flags&(1<<bit) != 0 {
						count++
					}
				}
				if len(features) != count || *result.UserData.SportAssociation.Logo != "converted:raw" {
					t.Fatal(features, result.UserData.SportAssociation)
				}
			}
		}
	}
}

func TestProfileUpdateResponseFailureFollowsCommit(t *testing.T) {
	store, id, input := profileMutationFixture()
	flow := NewProfileUpdates(NewProfileMutations(store, &profileImageTest{}, time.Now), NewProfileUpdateResponses(&responseProfiles{fail: true}, &responseAssociations{}, &responseImages{}, domain.RegistrationChoices{}))
	if _, err := flow.Update(context.Background(), id, input, domain.LoginAuditContext{}); err == nil || !store.tx.committed {
		t.Fatal("response failure changed transaction boundary", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := flow.Update(ctx, id, input, domain.LoginAuditContext{}); !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
}

func TestProfileUpdateResponsePreservesCommittedPersonalFields(t *testing.T) {
	store, id, input := profileMutationFixture()
	store.tx.person.Role = 2
	id.Effective.Role = 2
	laterAvatar := "later-avatar"
	profiles := &responseProfiles{profile: &dto.UserAuthProfile{UserID: "owner", FirstName: "Later", LastName: "Writer", Username: "LATER", AvatarImage: &laterAvatar}}
	flow := NewProfileUpdates(NewProfileMutations(store, &profileImageTest{}, time.Now), NewProfileUpdateResponses(profiles, nil, &responseImages{}, domain.RegistrationChoices{}))
	result, err := flow.Update(context.Background(), id, input, domain.LoginAuditContext{})
	if err != nil || result.UserData.FirstName != "Next" || result.UserData.LastName != "Last" || result.UserData.Username != "STRASSE" || result.UserData.AvatarImage == nil || *result.UserData.AvatarImage != "converted:compressed:raw" {
		t.Fatal("post-commit reader replaced own mutation", result, err)
	}
	if profiles.profile.FirstName != "Later" || *profiles.profile.AvatarImage != "later-avatar" {
		t.Fatal("reader-owned profile mutated")
	}
}

func TestProfileUpdateCollaboratorResponseRejectsForeignScope(t *testing.T) {
	for _, tc := range []struct {
		name                    string
		connected               *string
		scope, associationScope string
		calls                   int
	}{
		{"missing owner", nil, "owner", "", 0},
		{"different owner", func() *string { v := "foreign"; return &v }(), "owner", "", 0},
		{"different impersonation tenant", func() *string { v := "owner"; return &v }(), "owner", "foreign-association", 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			u := &domain.Account{ID: "collaborator", Role: domain.RoleCollaborator, ConnectedUserID: tc.connected}
			associations := &responseAssociations{profile: &dto.AssociationProfile{ID: "association"}}
			flow := NewProfileUpdateResponses(&responseProfiles{profile: &dto.UserAuthProfile{UserID: u.ID}}, associations, nil, domain.RegistrationChoices{})
			result, err := flow.Read(context.Background(), domain.ResolvedIdentity{Actor: u, Effective: u, ScopeUser: &domain.Account{ID: tc.scope}, ImpersonationAssociationID: tc.associationScope})
			if err == nil || result != nil || associations.calls != tc.calls {
				t.Fatalf("tenant guard: result=%v err=%v reads=%d", result, err, associations.calls)
			}
		})
	}
}

func TestProfileUpdateResponseUsesLockedConnectedOwner(t *testing.T) {
	for _, changed := range []bool{false, true} {
		t.Run(map[bool]string{false: "same tenant", true: "changed tenant"}[changed], func(t *testing.T) {
			store, id, input := profileMutationFixture()
			admitted, foreign := "admitted-owner", "foreign-owner"
			id.Effective.Role = domain.RoleCollaborator
			id.Effective.ConnectedUserID = &admitted
			id.ScopeUser = &domain.Account{ID: admitted, Role: domain.RoleAssociation}
			store.tx.person.Role = domain.RoleCollaborator
			store.tx.person.ConnectedUserID = &admitted
			if changed {
				store.tx.person.ConnectedUserID = &foreign
			}
			associations := &responseAssociations{profile: &dto.AssociationProfile{ID: "admitted-association"}}
			profiles := &responseProfiles{profile: &dto.UserAuthProfile{UserID: id.Effective.ID}}
			flow := NewProfileUpdates(NewProfileMutations(store, &profileImageTest{}, time.Now), NewProfileUpdateResponses(profiles, associations, &responseImages{}, domain.RegistrationChoices{}))
			result, err := flow.Update(context.Background(), id, input, domain.LoginAuditContext{})
			if !store.tx.committed || len(store.tx.associationWrites) != 0 || len(store.tx.audits) != 1 || store.tx.audits[0].Model != "user" {
				t.Fatal("changed mutation or audit scope", store.tx)
			}
			if changed {
				if !errors.Is(err, domain.ErrInvalidScopedRequest) || result != nil || associations.calls != 0 {
					t.Fatal("changed tenant disclosed", result, err, associations.calls)
				}
			} else {
				if err != nil || result.UserData.SportAssociation == nil || result.UserData.FirstName != "Next" || associations.calls != 1 {
					t.Fatal("same-tenant result", result, err)
				}
			}
			if *id.Effective.ConnectedUserID != admitted {
				t.Fatal("mutated admission snapshot")
			}
		})
	}
}
