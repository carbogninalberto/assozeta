package service

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// ErrCarnetListUnserializable is the view's 500 for roles it does not serve
// (`[].data`) and for a holding without a carnet.
var ErrCarnetListUnserializable = errors.New("carnet list cannot be serialized")

type CarnetLists struct {
	store domain.CarnetListStore
	now   func() time.Time
}

func NewCarnetLists(store domain.CarnetListStore, now func() time.Time) *CarnetLists {
	return &CarnetLists{store: store, now: now}
}

// List follows carnet_list: associations get their carnets, athletes the
// carnet subscriptions they hold, through scoped_queryset.
func (s *CarnetLists) List(ctx context.Context, identity domain.ResolvedIdentity, groupHeader string) (any, error) {
	user := identity.ScopeUser
	scope := paymentCategoryAssociationScope(identity)
	switch user.Role {
	case domain.RoleAssociation:
		rows, err := s.store.OwnerCarnets(ctx, user.ID, scope)
		if err != nil {
			return nil, err
		}
		items := make([]dto.CarnetListItem, 0, len(rows))
		for _, row := range rows {
			items = append(items, dto.CarnetListItemFrom(row))
		}
		return map[string]any{"data": items}, nil
	case domain.RoleAthlete:
		var holdings *domain.CarnetHoldingScope
		if scope != nil {
			person := identity.Effective
			if person == nil {
				person = user
			}
			holdings = &domain.CarnetHoldingScope{AssociationID: *scope, Family: person.Role == domain.RoleAthlete}
			if id, err := paymentInfoParseUUID(groupHeader); groupHeader != "" && err == nil {
				text := id.String()
				holdings.GroupID = &text
			}
		}
		rows, err := s.store.Holdings(ctx, user.ID, holdings)
		if err != nil {
			return nil, err
		}
		today := s.now().UTC()
		items := make([]dto.CarnetHolding, 0, len(rows))
		for _, row := range rows {
			if row.CarnetID == nil || (row.AssociateID != nil && (row.Sex == nil || row.IsMinor == nil)) {
				return nil, ErrCarnetListUnserializable
			}
			item, err := dto.CarnetHoldingFrom(row, today)
			if err != nil {
				return nil, err
			}
			items = append(items, item)
		}
		return map[string]any{"data": items}, nil
	default:
		return nil, ErrCarnetListUnserializable
	}
}

// ErrCarnetNotAllowed is carnet_info's {'msg': 'Not allowed.'} for non-association users.
var ErrCarnetNotAllowed = errors.New("carnet not allowed")

type CarnetInfos struct {
	store domain.CarnetInfoStore
	now   func() time.Time
}

func NewCarnetInfos(store domain.CarnetInfoStore, now func() time.Time) *CarnetInfos {
	return &CarnetInfos{store: store, now: now}
}

// Info follows carnet_info: the scope user's carnet and its holdings, each
// with the courses of its subscription.
func (s *CarnetInfos) Info(ctx context.Context, identity domain.ResolvedIdentity, raw string) (map[string]any, error) {
	user := identity.ScopeUser
	if user.Role != domain.RoleAssociation {
		return nil, ErrCarnetNotAllowed
	}
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return nil, err
	}
	scope := paymentCategoryAssociationScope(identity)
	carnet, err := s.store.OwnedCarnet(ctx, user.ID, id, scope)
	if err != nil {
		return nil, err
	}
	holdings, err := s.store.CarnetHoldings(ctx, carnet.ID, scope)
	if err != nil {
		return nil, err
	}
	for _, row := range holdings {
		if row.CarnetID == nil || (row.AssociateID != nil && (row.Sex == nil || row.IsMinor == nil)) {
			return nil, ErrCarnetListUnserializable
		}
	}
	info, err := dto.CarnetInfoFrom(carnet, holdings, s.now().UTC())
	if err != nil {
		return nil, err
	}
	return map[string]any{"data": info}, nil
}
