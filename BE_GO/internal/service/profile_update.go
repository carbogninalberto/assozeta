package service

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"golang.org/x/text/cases"
	"golang.org/x/text/language"
)

type ProfileMutations struct {
	store  domain.ProfileUpdateStore
	images domain.ProfileUpdateImages
	now    func() time.Time
}

func NewProfileMutations(store domain.ProfileUpdateStore, images domain.ProfileUpdateImages, now func() time.Time) *ProfileMutations {
	return &ProfileMutations{store, images, now}
}

func (s *ProfileMutations) Change(ctx context.Context, identity domain.ResolvedIdentity, input dto.ProfileUpdateRequest, audit domain.LoginAuditContext) (domain.ProfileUpdatePerson, error) {
	empty := domain.ProfileUpdatePerson{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if identity.Actor == nil || identity.Effective == nil || identity.ScopeUser == nil || identity.Effective.ID == "" {
		return empty, domain.ErrInvalidScopedRequest
	}
	for key := range input.Person {
		switch key {
		case "first_name", "last_name", "username", "avatar_image":
		default:
			return empty, dto.ErrProfileUpdateShape
		}
	}
	audit.ActorID = &identity.Actor.ID
	audit.ActorEmail = &identity.Actor.Email
	audit.AdditionalData = nil
	var scope *string
	if identity.ImpersonationAssociationID != "" {
		value := identity.ImpersonationAssociationID
		scope = &value
	}
	if identity.Impersonating {
		audit.AdditionalData = &domain.AuditImpersonation{SessionID: identity.SessionID, ActorID: identity.Actor.ID, TargetID: identity.Effective.ID, AssociationID: scope}
	}
	for attempt := 0; attempt < 3; attempt++ {
		result, err := s.changeAttempt(ctx, identity, input, audit, scope)
		if !errors.Is(err, domain.ErrCredentialConflict) {
			return result, err
		}
	}
	return empty, domain.ErrCredentialConflict
}

func (s *ProfileMutations) changeAttempt(ctx context.Context, identity domain.ResolvedIdentity, input dto.ProfileUpdateRequest, audit domain.LoginAuditContext, scope *string) (domain.ProfileUpdatePerson, error) {
	empty := domain.ProfileUpdatePerson{}
	tx, err := s.store.BeginProfileUpdate(ctx)
	if err != nil {
		return empty, err
	}
	defer tx.Rollback()
	person, err := tx.ProfilePersonForUpdate(ctx, identity.Effective.ID)
	if err != nil {
		return empty, err
	}
	if person.ID != identity.Effective.ID {
		return empty, domain.ErrInvalidScopedRequest
	}
	var avatar any
	if person.AvatarImage != nil {
		avatar = *person.AvatarImage
	}
	before := map[string]any{"first_name": person.FirstName, "last_name": person.LastName, "username": person.Username, "avatar_image": avatar}
	fields := map[string]any{}
	changes := map[string][2]string{}
	for _, key := range []string{"first_name", "last_name", "username", "avatar_image"} {
		value, ok := input.Person[key]
		if !ok || value == nil {
			continue
		}
		if key == "username" {
			text, ok := value.(string)
			if !ok {
				return empty, dto.ErrProfileUpdateShape
			}
			value = cases.Upper(language.Und).String(text)
		}
		if key == "avatar_image" {
			if text, ok := value.(string); ok {
				if s.images == nil {
					return empty, ErrLoginImageProcessorUnavailable
				}
				value, err = s.images.CompressProfileImage(ctx, text)
				if err != nil {
					return empty, err
				}
			}
		}
		text, err := loginValueText(ctx, value)
		if err != nil {
			return empty, err
		}
		old, err := loginValueText(ctx, before[key])
		if err != nil {
			return empty, err
		}
		fields[key] = text
		if old != text {
			changes[key] = [2]string{old, text}
		}
		switch key {
		case "first_name":
			person.FirstName = text
		case "last_name":
			person.LastName = text
		case "username":
			person.Username = text
		case "avatar_image":
			person.AvatarImage = &text
		}
	}
	if len(fields) > 0 {
		if err = tx.SetProfilePerson(ctx, person.ID, fields); err != nil {
			return empty, err
		}
	}
	if len(changes) > 0 {
		associations, err := tx.AuditAssociations(ctx, person.ID, audit.ActorID)
		if err != nil {
			return empty, err
		}
		event := domain.ProfileUpdateAudit{Model: "user", ID: person.ID, ObjectRepresentation: "name: " + person.FirstName + " " + person.LastName, Changes: changes, Context: audit, At: s.now()}
		association, path := associations[person.ID], "user.sport_association"
		if association == "" {
			association = associations[*audit.ActorID]
			path = "actor.sport_association"
		}
		if association != "" {
			event.AssociationID = &association
			event.ResolutionPath = &path
		}
		if err = tx.AppendProfileAudit(ctx, event); err != nil {
			return empty, err
		}
	}
	if input.Association != nil && person.Role == domain.RoleAssociation {
		if identity.ScopeUser.ID != person.ID {
			return empty, domain.ErrInvalidScopedRequest
		}
		fields, err := dto.ParseProfileAssociationUpdate(input.Association)
		if err != nil {
			return empty, err
		}
		association, err := tx.ProfileAssociationForUpdate(ctx, person.ID, scope)
		if err != nil {
			return empty, err
		}
		if association.ID == "" || association.OwnerID != person.ID || scope != nil && association.ID != *scope {
			return empty, domain.ErrAssociationNotFound
		}
		writes, changes, err := profileAssociationFields(ctx, association.Values, fields)
		if err != nil {
			return empty, err
		}
		if err = tx.SetProfileAssociation(ctx, association.ID, person.ID, writes); err != nil {
			return empty, err
		}
		if len(changes) > 0 {
			path := "self"
			event := domain.ProfileUpdateAudit{Model: "sportassociation", ID: association.ID, ObjectRepresentation: "SportAssociation object (" + association.ID + ")", Changes: changes, Context: audit, AssociationID: &association.ID, ResolutionPath: &path, At: s.now()}
			if err = tx.AppendProfileAudit(ctx, event); err != nil {
				return empty, err
			}
		}
	}
	if err = ctx.Err(); err != nil {
		return empty, err
	}
	if err = tx.Commit(); err != nil {
		return empty, err
	}
	return person, nil
}

func profileAssociationFields(ctx context.Context, before, fields map[string]any) (map[string]any, map[string][2]string, error) {
	writes := make(map[string]any, len(fields))
	changes := make(map[string][2]string)
	for key, value := range fields {
		var old, next string
		var err error
		switch key {
		case "configuration", "stripe_available_methods":
			old, err = profileJSONText(ctx, before[key])
			if err != nil {
				return nil, nil, err
			}
			next, err = profileJSONText(ctx, value)
			writes[key] = json.RawMessage(next)
		default:
			old, err = loginValueText(ctx, before[key])
			if err != nil {
				return nil, nil, err
			}
			next, err = loginValueText(ctx, value)
			if key == "enable_quotes_management" {
				if err == nil {
					writes[key], err = profileBoolean(value)
				}
			} else if value == nil {
				writes[key] = nil
			} else {
				writes[key] = next
			}
		}
		if err != nil {
			return nil, nil, err
		}
		if old != next {
			changes[key] = [2]string{old, next}
		}
	}
	return writes, changes, nil
}
