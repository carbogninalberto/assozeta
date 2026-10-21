package service

import (
	"context"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/jwt"
)

var ErrLoginResponseRole = errors.New("unsupported stored login role")
var ErrLoginImageProcessorUnavailable = errors.New("login image processor unavailable")

type LoginResponseProfiles interface {
	Read(context.Context, string) (*dto.UserAuthProfile, error)
}
type LoginResponseAssociations interface {
	Read(context.Context, string) (*dto.AssociationProfile, error)
}
type LoginResponseCards interface {
	ForLogin(context.Context, domain.Account, string, domain.LoginAuditContext) (*dto.MembershipCardConfiguration, error)
}
type LoginResponses struct {
	profiles     LoginResponseProfiles
	associations LoginResponseAssociations
	settings     domain.LoginResponseSettingsReader
	cards        LoginResponseCards
	images       domain.LoginImageProcessor
	choices      domain.RegistrationChoices
}

func NewLoginResponses(profiles LoginResponseProfiles, associations LoginResponseAssociations, settings domain.LoginResponseSettingsReader, cards LoginResponseCards, images domain.LoginImageProcessor, choices domain.RegistrationChoices) *LoginResponses {
	return &LoginResponses{profiles, associations, settings, cards, images, choices}
}

// Build composes response fields and the default-card invariant. Tokens are
// supplied by the login coordinator; it must still finalize user writes before
// delivering this result. Neither this service nor its role label grants access.
func (s *LoginResponses) Build(ctx context.Context, account domain.Account, tokens jwt.Pair, audit domain.LoginAuditContext) (*dto.LoginResponse, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	// PositiveSmallIntegerField permits zero. Python ROLE_CHOICES[role-1]
	// labels that historical value "association" without entering the role-1 branch.
	role := "association"
	switch account.Role {
	case 0, 1, 3:
	case 2:
		role = "athlete"
	default:
		return nil, ErrLoginResponseRole
	}
	profile, err := s.profiles.Read(ctx, account.ID)
	if err != nil {
		return nil, err
	}
	if profile == nil || profile.UserID != account.ID {
		return nil, domain.ErrAccountNotFound
	}
	result := &dto.LoginResponse{Pair: tokens, Role: role, UserData: dto.LoginUserProfile{UserAuthProfile: *profile}}
	yes, no := true, false
	if account.Superuser {
		result.Role = "administrator"
		result.UserData.IsSuperuser = &yes
		result.RequiresWelcome = &no
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		return result, nil
	}
	if account.Role == domain.RoleCollaborator {
		result.RequiresWelcome = &no
	}
	if account.Role != domain.RoleAssociation && account.Role != domain.RoleCollaborator {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		return result, nil
	}
	owner := account.ID
	if account.Role == domain.RoleCollaborator {
		if account.ConnectedUserID == nil {
			return nil, domain.ErrAssociationNotFound
		}
		owner = *account.ConnectedUserID
	}
	association, err := s.associations.Read(ctx, owner)
	if err != nil {
		return nil, err
	}
	if association == nil || association.ID == "" {
		return nil, domain.ErrAssociationNotFound
	}
	settings, err := s.settings.LoginResponseSettings(ctx, account.ID, account.Role == domain.RoleCollaborator)
	if err != nil {
		return nil, err
	}
	if settings.UserID != account.ID {
		return nil, domain.ErrAccountNotFound
	}
	if account.Role == domain.RoleCollaborator {
		result.UserData.InstructorID = settings.InstructorID
	}
	result.UserData.TemporaryInvoiceDeletion = &settings.TemporaryInvoiceDeletion
	result.UserData.SportAssociation = &dto.LoginAssociationProfile{AssociationProfile: *association}
	if profile.AvatarImage != nil {
		value, err := s.compress(ctx, *profile.AvatarImage)
		if err != nil {
			return nil, err
		}
		result.UserData.AvatarImage = &value
	}
	empty := func(value *string) bool { return value == nil || *value == "" }
	if empty(association.Regulation) || empty(association.Demand) {
		result.UserData.SportAssociation.EmptySections = &yes
		if association.Logo != nil {
			value, err := s.compress(ctx, *association.Logo)
			if err != nil {
				return nil, err
			}
			result.UserData.SportAssociation.Logo = &value
		}
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
	card, err := s.cards.ForLogin(ctx, account, association.ID, audit)
	if err != nil {
		return nil, err
	}
	if card == nil {
		return nil, domain.ErrAssociationNotFound
	}
	result.UserData.SportAssociation.MembershipCardConfiguration = card
	result.TablesSettings = settings.TablesSettings
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return result, nil
}
func (s *LoginResponses) compress(ctx context.Context, value string) (string, error) {
	if s.images == nil {
		return "", ErrLoginImageProcessorUnavailable
	}
	return s.images.CompressLoginImage(ctx, value)
}
