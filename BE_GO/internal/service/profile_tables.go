package service

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type ProfileTables struct {
	store domain.ProfileTablesStore
	now   func() time.Time
}

func NewProfileTables(store domain.ProfileTablesStore, now func() time.Time) *ProfileTables {
	return &ProfileTables{store, now}
}

func (s *ProfileTables) Update(ctx context.Context, identity domain.ResolvedIdentity, input dto.ProfileTablesRequest, audit domain.LoginAuditContext) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if identity.Actor == nil || identity.Actor.ID == "" || identity.Effective == nil || identity.Effective.ID == "" || identity.ScopeUser == nil || identity.ScopeUser.ID == "" {
		return domain.ErrInvalidScopedRequest
	}
	owner := identity.Effective.ID
	if identity.Effective.Role == domain.RoleCollaborator {
		if !identity.Collaborator || identity.Effective.ConnectedUserID == nil {
			return domain.ErrInvalidScopedRequest
		}
		owner = *identity.Effective.ConnectedUserID
	}
	if identity.ScopeUser.ID != owner {
		return domain.ErrInvalidScopedRequest
	}
	if !input.Present {
		return nil
	}
	after, err := profileJSONText(ctx, input.Value)
	if err != nil {
		return err
	}
	var stored json.RawMessage
	if input.Value != nil {
		stored = json.RawMessage(after)
	}
	audit.ActorID = &identity.Actor.ID
	audit.ActorEmail = &identity.Actor.Email
	audit.AdditionalData = nil
	if identity.Impersonating {
		var association *string
		if identity.ImpersonationAssociationID != "" {
			value := identity.ImpersonationAssociationID
			association = &value
		}
		audit.AdditionalData = &domain.AuditImpersonation{SessionID: identity.SessionID, ActorID: identity.Actor.ID, TargetID: identity.Effective.ID, AssociationID: association}
	}
	for attempt := 0; attempt < 3; attempt++ {
		err = s.updateAttempt(ctx, owner, stored, after, audit)
		if !errors.Is(err, domain.ErrCredentialConflict) {
			return err
		}
	}
	return domain.ErrCredentialConflict
}
func (s *ProfileTables) updateAttempt(ctx context.Context, owner string, stored json.RawMessage, after string, audit domain.LoginAuditContext) error {
	tx, err := s.store.BeginProfileTables(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	row, err := tx.TablePreferencesForUpdate(ctx, owner)
	if err != nil {
		return err
	}
	if row.ID != owner {
		return domain.ErrInvalidScopedRequest
	}
	before, err := dto.ParseReferenceJSON(row.Tables)
	if err != nil {
		return err
	}
	previous, err := profileJSONText(ctx, before)
	if err != nil {
		return err
	}
	if err = tx.SetTablePreferences(ctx, owner, stored); err != nil {
		return err
	}
	if previous != after {
		associations, err := tx.AuditAssociations(ctx, owner, audit.ActorID)
		if err != nil {
			return err
		}
		event := domain.ProfileUpdateAudit{Model: "user", ID: owner, ObjectRepresentation: "name: " + row.FirstName + " " + row.LastName, Changes: map[string][2]string{"tables_settings": {previous, after}}, Context: audit, At: s.now()}
		association, path := associations[owner], "user.sport_association"
		if association == "" {
			association = associations[*audit.ActorID]
			path = "actor.sport_association"
		}
		if association != "" {
			event.AssociationID = &association
			event.ResolutionPath = &path
		}
		if err = tx.AppendProfileAudit(ctx, event); err != nil {
			return err
		}
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}
