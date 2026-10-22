package service

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type ProfileUpdateResponses struct {
	profiles     LoginResponseProfiles
	associations LoginResponseAssociations
	images       domain.LoginImageProcessor
	choices      domain.RegistrationChoices
}

func NewProfileUpdateResponses(profiles LoginResponseProfiles, associations LoginResponseAssociations, images domain.LoginImageProcessor, choices domain.RegistrationChoices) *ProfileUpdateResponses {
	return &ProfileUpdateResponses{profiles, associations, images, choices}
}
func (s *ProfileUpdateResponses) Read(ctx context.Context, identity domain.ResolvedIdentity) (*dto.ProfileUpdateResponse, error) {
	return s.read(ctx, identity, nil)
}

func (s *ProfileUpdateResponses) read(ctx context.Context, identity domain.ResolvedIdentity, committed *domain.ProfileUpdatePerson) (*dto.ProfileUpdateResponse, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if identity.Actor == nil || identity.Effective == nil || identity.ScopeUser == nil || identity.Effective.ID == "" {
		return nil, domain.ErrInvalidScopedRequest
	}
	user := identity.Effective
	role := "association"
	switch user.Role {
	case -2, 0, 1, 3:
	case -1, 2:
		role = "athlete"
	default:
		return nil, ErrLoginResponseRole
	}
	profile, err := s.profiles.Read(ctx, user.ID)
	if err != nil {
		return nil, err
	}
	if profile == nil || profile.UserID != user.ID {
		return nil, domain.ErrAccountNotFound
	}
	result := &dto.ProfileUpdateResponse{Role: role, UserData: dto.ProfileUpdateUser{UserAuthProfile: *profile}}
	if committed != nil {
		if committed.ID != user.ID {
			return nil, domain.ErrInvalidScopedRequest
		}
		// Django serializes these fields from the locked, updated User instance.
		// A later writer must not replace this request's committed personal values.
		result.UserData.FirstName = committed.FirstName
		result.UserData.LastName = committed.LastName
		result.UserData.Username = committed.Username
		result.UserData.AvatarImage = committed.AvatarImage
	}
	compress := func(value *string) (*string, error) {
		if value == nil {
			return nil, nil
		}
		if s.images == nil {
			return nil, ErrLoginImageProcessorUnavailable
		}
		text, err := s.images.CompressLoginImage(ctx, *value)
		if err != nil {
			return nil, err
		}
		return &text, nil
	}
	result.UserData.AvatarImage, err = compress(result.UserData.AvatarImage)
	if err != nil {
		return nil, err
	}
	if user.Role == domain.RoleAssociation {
		if identity.ScopeUser.ID != user.ID {
			return nil, domain.ErrInvalidScopedRequest
		}
		association, err := s.associations.Read(ctx, user.ID)
		if err != nil {
			return nil, err
		}
		if association == nil || association.ID == "" || identity.ImpersonationAssociationID != "" && association.ID != identity.ImpersonationAssociationID {
			return nil, domain.ErrAssociationNotFound
		}
		copy := *association
		copy.Logo, err = compress(association.Logo)
		if err != nil {
			return nil, err
		}
		result.UserData.SportAssociation = &copy
		features := make([]dto.RegistrationFeature, 0, 3)
		for _, choice := range []struct {
			enabled bool
			name    string
		}{{s.choices.Family, "Iscrizione per famiglia"}, {s.choices.Federation, "Iscrizioni per Enti e Federazioni"}, {s.choices.Multiple, "Iscrizioni multiple per federazioni"}} {
			if choice.enabled {
				features = append(features, dto.RegistrationFeature{Name: choice.name, Description: ""})
			}
		}
		result.UserData.PreviewAndCustomFeatures = &features
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return result, nil
}

type ProfileUpdates struct {
	mutations *ProfileMutations
	responses *ProfileUpdateResponses
}

func NewProfileUpdates(mutations *ProfileMutations, responses *ProfileUpdateResponses) *ProfileUpdates {
	return &ProfileUpdates{mutations, responses}
}
func (s *ProfileUpdates) Update(ctx context.Context, identity domain.ResolvedIdentity, input dto.ProfileUpdateRequest, audit domain.LoginAuditContext) (*dto.ProfileUpdateResponse, error) {
	state, err := s.mutations.Change(ctx, identity, input, audit)
	if err != nil {
		return nil, err
	}
	// The repaired Django view commits the invariant before response serialization.
	// Use the role re-read under the mutation lock, not a stale admission snapshot.
	person := *identity.Effective
	person.Role = state.Role
	identity.Effective = &person
	return s.responses.read(ctx, identity, &state)
}
