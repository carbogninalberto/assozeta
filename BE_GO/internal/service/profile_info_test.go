package service

import (
	"context"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type profileInfoDetailsStub struct {
	row         domain.ProfileInfoDetails
	fail        bool
	calls       int
	user, owner string
}

func (s *profileInfoDetailsStub) Read(_ context.Context, user, owner string) (domain.ProfileInfoDetails, error) {
	s.calls++
	s.user = user
	s.owner = owner
	if s.fail {
		return domain.ProfileInfoDetails{}, errors.New("details failed")
	}
	return s.row, nil
}
func TestProfileInfoIdentityImagesAndFailures(t *testing.T) {
	for _, kind := range []string{"collaborator", "athlete", "administrator", "missing identity", "foreign profile", "foreign association", "foreign details", "wrong owner", "profile failure", "association failure", "details failure", "image failure", "cancelled"} {
		t.Run(kind, func(t *testing.T) {
			owner := "owner"
			avatar, logo, present := "avatar", "logo", "present"
			actor := &domain.Account{ID: "actor", Active: true, Superuser: true}
			effective := &domain.Account{ID: "person", Role: domain.RoleCollaborator, Active: true, ConnectedUserID: &owner}
			scope := &domain.Account{ID: owner, Role: domain.RoleAssociation, Active: true}
			identity := domain.ResolvedIdentity{Actor: actor, Effective: effective, ScopeUser: scope, Collaborator: true, Impersonating: true, ImpersonationAssociationID: "association"}
			profiles := &responseProfiles{profile: &dto.UserAuthProfile{UserID: "person", AvatarImage: &avatar}}
			associations := &responseAssociations{profile: &dto.AssociationProfile{ID: "association", Logo: &logo, Regulation: &present, Demand: &present}}
			details := &profileInfoDetailsStub{row: domain.ProfileInfoDetails{UserID: "person", AssociationID: "association", TemporaryInvoiceDeletion: true}}
			images := &responseImages{}
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			switch kind {
			case "athlete":
				effective.Role = domain.RoleAthlete
				identity.ScopeUser = effective
				identity.Collaborator = false
			case "administrator":
				effective.Superuser = true
				identity.ScopeUser = effective
				identity.Collaborator = false
			case "missing identity":
				identity.Effective = nil
			case "foreign profile":
				profiles.profile.UserID = "foreign"
			case "foreign association":
				associations.profile.ID = "foreign"
			case "foreign details":
				details.row.UserID = "foreign"
			case "wrong owner":
				identity.ScopeUser.ID = "foreign"
			case "profile failure":
				profiles.fail = true
			case "association failure":
				associations.fail = true
			case "details failure":
				details.fail = true
			case "image failure":
				images.fail = true
			case "cancelled":
				cancel()
			}
			result, err := NewProfileInfo(profiles, associations, details, images, domain.RegistrationChoices{Family: true}).Read(ctx, identity)
			if kind != "collaborator" && kind != "athlete" && kind != "administrator" {
				if err == nil || result != nil {
					t.Fatal("failed read exposed profile")
				}
				if kind == "cancelled" && profiles.calls != 0 {
					t.Fatal("cancelled request reached data reader")
				}
				return
			}
			if err != nil || result.UserData.UserID != "person" {
				t.Fatal("effective profile differs", err)
			}
			if kind == "administrator" {
				if result.Info.Role != "administrator" || result.UserData.IsSuperuser == nil || !*result.UserData.IsSuperuser || len(images.inputs) != 0 || associations.calls != 0 || details.calls != 0 {
					t.Fatal("admin response performed ordinary profile work")
				}
				return
			}
			if *result.UserData.AvatarImage != "converted:avatar" {
				t.Fatal("avatar not compressed")
			}
			if kind == "athlete" {
				if result.Info.Role != "athlete" || associations.calls != 0 || details.calls != 0 || result.UserData.SportAssociation != nil {
					t.Fatal("athlete read association details")
				}
				return
			}
			if associations.owner != "owner" || details.user != "person" || details.owner != "owner" || result.Info.Role != "association" || len(images.inputs) != 2 || *result.UserData.SportAssociation.Logo != "converted:logo" || result.UserData.SportAssociation.EmptySections != nil {
				t.Fatal("collaborator identity or complete-section image behavior differs")
			}
			if result.UserData.PreviewAndCustomFeatures == nil || len(*result.UserData.PreviewAndCustomFeatures) != 1 {
				t.Fatal("registration choices differ")
			}
		})
	}
}

type profileInfoCancellingReader struct{ cancel context.CancelFunc }

func (s profileInfoCancellingReader) Read(context.Context, string) (*dto.UserAuthProfile, error) {
	s.cancel()
	return &dto.UserAuthProfile{UserID: "person"}, nil
}
func TestProfileInfoCancelledAdminHasNoPartialResponse(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	account := &domain.Account{ID: "person", Role: domain.RoleAssociation, Superuser: true}
	value, err := NewProfileInfo(profileInfoCancellingReader{cancel}, nil, nil, nil, domain.RegistrationChoices{}).Read(ctx, domain.ResolvedIdentity{Actor: account, Effective: account, ScopeUser: account})
	if !errors.Is(err, context.Canceled) || value != nil {
		t.Fatal("cancelled administrator read returned partial response")
	}
}
