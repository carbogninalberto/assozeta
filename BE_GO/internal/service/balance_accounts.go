package service

import (
	"context"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// ErrBalanceInfoUnavailable is the views' {'msg': 'Info not available.'}.
var ErrBalanceInfoUnavailable = errors.New("balance info not available")

type BalanceAccounts struct{ store domain.BalanceAccountStore }

func NewBalanceAccounts(store domain.BalanceAccountStore) *BalanceAccounts {
	return &BalanceAccounts{store: store}
}

// association applies IsAssociationUser, the views' role check and
// SportAssociation.objects.get(user=request.user).
func (s *BalanceAccounts) association(ctx context.Context, identity domain.ResolvedIdentity) (string, error) {
	switch identity.ScopeUser.Role {
	case domain.RoleAssociation:
	case domain.RoleCollaborator:
		return "", ErrBalanceInfoUnavailable
	default:
		return "", domain.ErrMessageReadForbidden
	}
	return s.store.OwnerAssociation(ctx, identity.ScopeUser.ID)
}

func (s *BalanceAccounts) Accounts(ctx context.Context, identity domain.ResolvedIdentity, related, groupHeader string) (map[string]any, error) {
	association, err := s.association(ctx, identity)
	if err != nil {
		return nil, err
	}
	serialized := related == "true"
	var group *string
	if serialized {
		group = supplierGroup(groupHeader)
	}
	rows, err := s.store.Accounts(ctx, association, group, serialized)
	if err != nil {
		return nil, err
	}
	items := make([]any, 0, len(rows))
	for _, row := range rows {
		var item any
		if serialized {
			item, err = dto.BalanceAccountItemFrom(row)
		} else {
			item, err = dto.BalanceAccountRawFrom(row)
		}
		if err != nil {
			return nil, err
		}
		items = append(items, item)
	}
	return map[string]any{"data": items}, nil
}

func (s *BalanceAccounts) Transfers(ctx context.Context, identity domain.ResolvedIdentity) (map[string]any, error) {
	association, err := s.association(ctx, identity)
	if err != nil {
		return nil, err
	}
	rows, err := s.store.Transfers(ctx, association)
	if err != nil {
		return nil, err
	}
	items := make([]dto.BalanceTransferItem, 0, len(rows))
	for _, row := range rows {
		items = append(items, dto.BalanceTransferItemFrom(row))
	}
	return map[string]any{"data": items}, nil
}
