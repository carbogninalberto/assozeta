package service

import (
	"context"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type MessageReads struct{ store domain.MessageReadStore }

func NewMessageReads(store domain.MessageReadStore) *MessageReads {
	return &MessageReads{store: store}
}

// Admit enforces the view's association-user permission from the trusted
// resolved identity. Transport calls it before OPTIONS and method dispatch.
func (s *MessageReads) Admit(ctx context.Context, identity domain.ResolvedIdentity) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	_, err := messageReadsOwner(identity)
	return err
}

func (s *MessageReads) List(ctx context.Context, identity domain.ResolvedIdentity) ([]dto.MessageRead, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	owner, err := messageReadsOwner(identity)
	if err != nil {
		return nil, err
	}
	rows, err := s.store.ListMessages(ctx, owner)
	if err != nil {
		return nil, err
	}
	result := make([]dto.MessageRead, 0, len(rows))
	for _, row := range rows {
		result = append(result, dto.MessageReadFromRecord(row))
	}
	return result, nil
}

func (s *MessageReads) Detail(ctx context.Context, identity domain.ResolvedIdentity, identifier string) ([]dto.MessageTransactionRead, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	owner, err := messageReadsOwner(identity)
	if err != nil {
		return nil, err
	}
	id, err := messageReadsParseUUID(identifier)
	if err != nil {
		return nil, err
	}
	rows, err := s.store.MessageTransactions(ctx, owner, id)
	if err != nil {
		return nil, err
	}
	if len(rows) == 0 {
		return nil, domain.ErrMessageReadNotFound
	}
	result := make([]dto.MessageTransactionRead, 0, len(rows))
	for _, row := range rows {
		result = append(result, dto.MessageTransactionReadFromRecord(row))
	}
	return result, nil
}

func messageReadsOwner(identity domain.ResolvedIdentity) (string, error) {
	if identity.ScopeUser == nil || identity.ScopeUser.ID == "" ||
		(identity.ScopeUser.Role != domain.RoleAssociation && identity.ScopeUser.Role != domain.RoleCollaborator) {
		return "", domain.ErrMessageReadForbidden
	}
	return identity.ScopeUser.ID, nil
}

func messageReadsParseUUID(raw string) (string, error) {
	id, err := paymentInfoParseUUID(raw)
	if err == nil {
		return id.String(), nil
	}
	var invalid *PaymentInfoUUIDError
	if errors.As(err, &invalid) {
		return "", &dto.MessageReadUUIDError{Detail: invalid.Detail}
	}
	return "", err
}
