package service

import (
	"context"
	"encoding/json"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type personaSubscriptionsRouterStore interface {
	RouterCountSubscriptions(ctx context.Context, query domain.PersonaSubscriptionQuery, id *string) (int64, error)
	RouterSubscriptions(ctx context.Context, query domain.PersonaSubscriptionQuery, id *string, limit, offset int64) ([]domain.PersonaSubscriptionRow, error)
}

// routerItems renders AssociateSubscriptionSerializer for rows of any
// associates: the associate is the forward relation (base manager), its
// nested tutors the live, group-aware M2M manager; get_age() on a
// subscription without an associate raises (500).
func (s *PersonaSubscriptions) routerItems(ctx context.Context, rows []domain.PersonaSubscriptionRow, group *string,
	today time.Time) ([]any, []any, error) {
	data, rowIDs := make([]any, 0, len(rows)), make([]any, 0, len(rows))
	for _, subscription := range rows {
		if subscription.AssociateID == nil {
			return nil, nil, ErrPersonaUnhandled
		}
		row, err := s.personas.store.PersonaByID(ctx, *subscription.AssociateID)
		if err != nil {
			return nil, nil, err
		}
		if row == nil {
			return nil, nil, ErrPersonaUnhandled
		}
		second, err := s.personas.store.Tutors(ctx, []string{row.ID}, group)
		if err != nil {
			return nil, nil, err
		}
		nested := make([]string, len(second))
		for i, relation := range second {
			nested[i] = relation.ID
		}
		third, err := s.personas.store.Tutors(ctx, nested, group)
		if err != nil {
			return nil, nil, err
		}
		data = append(data, dto.PersonaSubscriptionItem(subscription, row.PersonaAssociate, second, third, today))
		rowIDs = append(rowIDs, row.ID)
	}
	return data, rowIDs, nil
}

// RouterList is AssociateSubscriptionViewSet.list(request) on the router's
// list route: pk is None, so every live subscription of the association
// (group-aware) by -creation_date, KTDatatablePagination.
func (s *PersonaSubscriptions) RouterList(ctx context.Context, identity domain.ResolvedIdentity, query domain.RequestObject, header string) (any, error) {
	association, err := s.personas.association(ctx, identity)
	if err != nil {
		return nil, err
	}
	store := s.store.(personaSubscriptionsRouterStore)
	filter := domain.PersonaSubscriptionQuery{AssociationID: association, GroupID: supplierGroup(header), Today: s.personas.today()}
	count, err := store.RouterCountSubscriptions(ctx, filter, nil)
	if err != nil {
		return nil, err
	}
	number, size, pages, err := personaPage(count, query)
	if err != nil {
		return nil, err
	}
	offset, limit := int64(0), count
	if size.IsInt64() {
		offset = (number - 1) * size.Int64()
		limit = min(size.Int64(), count-offset)
	}
	rows := []domain.PersonaSubscriptionRow{}
	if limit > 0 {
		if rows, err = store.RouterSubscriptions(ctx, filter, nil, limit, offset); err != nil {
			return nil, err
		}
	}
	data, rowIDs, err := s.routerItems(ctx, rows, filter.GroupID, filter.Today)
	if err != nil {
		return nil, err
	}
	meta := domain.RequestObject{{Name: "total", Value: personaNumber(count)}, {Name: "page", Value: personaNumber(number)},
		{Name: "pages", Value: personaNumber(pages)}, {Name: "perpage", Value: json.Number(size.String())},
		{Name: "rowIds", Value: rowIDs}}
	return domain.RequestObject{{Name: "data", Value: data}, {Name: "meta", Value: meta}}, nil
}

// Locate is get_object(): get_queryset() (User.sport_association, live,
// group-aware), then the pk (a value the UUID field rejects is the bare 404).
func (s *PersonaSubscriptions) Locate(ctx context.Context, identity domain.ResolvedIdentity, rawID, header string) (domain.PersonaSubscriptionRow, error) {
	association, err := s.personas.association(ctx, identity)
	if err != nil {
		return domain.PersonaSubscriptionRow{}, err
	}
	id, err := paymentInfoParseUUID(rawID)
	if err != nil {
		return domain.PersonaSubscriptionRow{}, ErrPersonaInvalidPK
	}
	key := id.String()
	filter := domain.PersonaSubscriptionQuery{AssociationID: association, GroupID: supplierGroup(header), Today: s.personas.today()}
	rows, err := s.store.(personaSubscriptionsRouterStore).RouterSubscriptions(ctx, filter, &key, 1, 0)
	if err != nil {
		return domain.PersonaSubscriptionRow{}, err
	}
	if len(rows) == 0 {
		return domain.PersonaSubscriptionRow{}, domain.ErrPersonaSubscriptionNotFound
	}
	return rows[0], nil
}

// Retrieve is the stock retrieve(): get_object() and the serializer.
func (s *PersonaSubscriptions) Retrieve(ctx context.Context, identity domain.ResolvedIdentity, rawID, header string) (any, error) {
	row, err := s.Locate(ctx, identity, rawID, header)
	if err != nil {
		return nil, err
	}
	data, _, err := s.routerItems(ctx, []domain.PersonaSubscriptionRow{row}, supplierGroup(header), s.personas.today())
	if err != nil {
		return nil, err
	}
	return data[0], nil
}

// DeleteWithoutPK is the viewset's delete() on the list route: get_object()
// runs get_queryset() (User.sport_association), then asserts the pk.
func (s *PersonaSubscriptions) DeleteWithoutPK(ctx context.Context, identity domain.ResolvedIdentity) error {
	if _, err := s.personas.association(ctx, identity); err != nil {
		return err
	}
	return ErrPersonaUnhandled
}
