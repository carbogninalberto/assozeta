package service

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

type CollaboratorList struct{ reader domain.CollaboratorReader }

func NewCollaboratorList(reader domain.CollaboratorReader) *CollaboratorList {
	return &CollaboratorList{reader}
}
func (s *CollaboratorList) Read(ctx context.Context, identity domain.ResolvedIdentity, identifier *string) (any, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if identity.ScopeUser == nil || (identity.ScopeUser.Role != domain.RoleAssociation && identity.ScopeUser.Role != domain.RoleCollaborator) {
		return nil, &domain.PermissionDenied{Detail: "User is not a sport association"}
	}
	owner, err := uuid.Parse(identity.ScopeUser.ID)
	if err != nil {
		return nil, domain.ErrInvalidScopedRequest
	}
	if identifier != nil {
		id, err := paymentInfoParseUUID(*identifier)
		if err != nil {
			return nil, err
		}
		row, err := s.reader.Collaborator(ctx, owner, id)
		if err != nil {
			return nil, err
		}
		if row == nil {
			return dto.EmptyCollaborator(), nil
		}
		return dto.CollaboratorFromRecord(*row, false)
	}
	users, err := s.reader.Collaborators(ctx, owner)
	if err != nil {
		return nil, err
	}
	invites, err := s.reader.CollaborationInvites(ctx, owner)
	if err != nil {
		return nil, err
	}
	result := make([]any, 0, len(users)+len(invites))
	for _, user := range users {
		value, err := dto.CollaboratorFromRecord(user, true)
		if err != nil {
			return nil, err
		}
		result = append(result, value)
	}
	for _, invite := range invites {
		value, err := dto.CollaborationInviteFromRecord(invite)
		if err != nil {
			return nil, err
		}
		result = append(result, value)
	}
	return result, nil
}
