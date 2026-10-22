package service

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type ProfileInfo struct {
	profiles     LoginResponseProfiles
	associations LoginResponseAssociations
	details      domain.ProfileInfoDetailsReader
	images       domain.LoginImageProcessor
	choices      domain.RegistrationChoices
}

func NewProfileInfo(profiles LoginResponseProfiles, associations LoginResponseAssociations, details domain.ProfileInfoDetailsReader, images domain.LoginImageProcessor, choices domain.RegistrationChoices) *ProfileInfo {
	return &ProfileInfo{profiles, associations, details, images, choices}
}
func (s *ProfileInfo) Read(ctx context.Context, identity domain.ResolvedIdentity) (*dto.ProfileInfo, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if identity.Actor == nil || identity.Effective == nil || identity.ScopeUser == nil || identity.Effective.ID == "" {
		return nil, domain.ErrInvalidScopedRequest
	}
	user := identity.Effective
	role := "association"
	switch user.Role {
	case 0, 1, 3:
	case 2:
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
	result := &dto.ProfileInfo{Info: dto.ProfileInfoRole{Role: role}, UserData: dto.ProfileInfoUser{UserAuthProfile: *profile}}
	yes := true
	if user.Superuser {
		result.Info.Role = "administrator"
		result.UserData.IsSuperuser = &yes
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		return result, nil
	}
	if profile.AvatarImage != nil {
		value, err := s.compress(ctx, *profile.AvatarImage)
		if err != nil {
			return nil, err
		}
		result.UserData.AvatarImage = &value
	}
	if user.Role == domain.RoleAssociation || user.Role == domain.RoleCollaborator {
		owner := user.ID
		if user.Role == domain.RoleCollaborator {
			if user.ConnectedUserID == nil {
				return nil, domain.ErrAssociationNotFound
			}
			owner = *user.ConnectedUserID
		}
		if identity.ScopeUser.ID != owner {
			return nil, domain.ErrInvalidScopedRequest
		}
		association, err := s.associations.Read(ctx, owner)
		if err != nil {
			return nil, err
		}
		if association == nil || association.ID == "" || (identity.ImpersonationAssociationID != "" && association.ID != identity.ImpersonationAssociationID) {
			return nil, domain.ErrAssociationNotFound
		}
		details, err := s.details.Read(ctx, user.ID, owner)
		if err != nil {
			return nil, err
		}
		if details.UserID != user.ID || details.AssociationID != association.ID {
			return nil, domain.ErrAssociationNotFound
		}
		result.UserData.TemporaryInvoiceDeletion = &details.TemporaryInvoiceDeletion
		result.UserData.SportAssociation = &dto.ProfileInfoAssociation{AssociationProfile: *association, ReviewURL: details.ReviewURL, ReviewURLEnabled: details.ReviewURLEnabled}
		if association.Logo != nil {
			value, err := s.compress(ctx, *association.Logo)
			if err != nil {
				return nil, err
			}
			result.UserData.SportAssociation.Logo = &value
		}
		empty := func(value *string) bool { return value == nil || *value == "" }
		if empty(association.Regulation) || empty(association.Demand) {
			result.UserData.SportAssociation.EmptySections = &yes
		}
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
func (s *ProfileInfo) compress(ctx context.Context, value string) (string, error) {
	if s.images == nil {
		return "", ErrLoginImageProcessorUnavailable
	}
	return s.images.CompressLoginImage(ctx, value)
}
