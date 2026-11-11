package service

import (
	"context"
	"encoding/json"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// PersonaSubscriptions ports AssociateSubscriptionViewSet.
type PersonaSubscriptions struct {
	store    domain.PersonaSubscriptionsStore
	personas *Personas
}

func NewPersonaSubscriptions(store domain.PersonaSubscriptionsStore, personas *Personas) *PersonaSubscriptions {
	return &PersonaSubscriptions{store: store, personas: personas}
}

// List is list(pk): get_queryset().filter(associate_id=pk) (a pk the UUID
// field rejects is a ValidationError: 500), KTDatatablePagination and
// AssociateSubscriptionSerializer; rowIds are the subscriptions' associate ids.
func (s *PersonaSubscriptions) List(ctx context.Context, identity domain.ResolvedIdentity, rawID string, query domain.RequestObject, header string) (any, error) {
	association, err := s.personas.association(ctx, identity)
	if err != nil {
		return nil, err
	}
	associate, ok := dto.PersonaUUIDValue(rawID)
	if !ok {
		return nil, ErrPersonaUnhandled
	}
	filter := domain.PersonaSubscriptionQuery{AssociationID: association, AssociateID: associate, GroupID: supplierGroup(header),
		Today: s.personas.today()}
	count, err := s.store.CountSubscriptions(ctx, filter)
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
		if rows, err = s.store.Subscriptions(ctx, filter, limit, offset); err != nil {
			return nil, err
		}
	}
	data, rowIDs := make([]any, 0, len(rows)), make([]any, 0, len(rows))
	if len(rows) > 0 {
		// The associate is the forward relation (base manager); its nested
		// tutors are the M2M manager (live, group-aware).
		row, err := s.personas.store.PersonaByID(ctx, associate)
		if err != nil {
			return nil, err
		}
		if row == nil {
			return nil, ErrPersonaUnhandled
		}
		second, err := s.personas.store.Tutors(ctx, []string{row.ID}, filter.GroupID)
		if err != nil {
			return nil, err
		}
		nested := make([]string, len(second))
		for i, relation := range second {
			nested[i] = relation.ID
		}
		third, err := s.personas.store.Tutors(ctx, nested, filter.GroupID)
		if err != nil {
			return nil, err
		}
		for _, subscription := range rows {
			data = append(data, dto.PersonaSubscriptionItem(subscription, row.PersonaAssociate, second, third, filter.Today))
			rowIDs = append(rowIDs, row.ID)
		}
	}
	meta := domain.RequestObject{{Name: "total", Value: personaNumber(count)}, {Name: "page", Value: personaNumber(number)},
		{Name: "pages", Value: personaNumber(pages)}, {Name: "perpage", Value: json.Number(size.String())},
		{Name: "rowIds", Value: rowIDs}}
	return domain.RequestObject{{Name: "data", Value: data}, {Name: "meta", Value: meta}}, nil
}

// Delete is the viewset's delete(): get_object() on get_queryset() by pk (a
// value the UUID field rejects is a bare 404); the soft delete of a found
// subscription is not ported (Subscription.save() derives missing dates from
// the association's settings) and is refused before any write.
func (s *PersonaSubscriptions) Delete(ctx context.Context, identity domain.ResolvedIdentity, rawID string, header string, login domain.LoginAuditContext) error {
	association, err := s.personas.association(ctx, identity)
	if err != nil {
		return err
	}
	id, err := paymentInfoParseUUID(rawID)
	if err != nil {
		return ErrPersonaInvalidPK
	}
	found, err := s.store.SubscriptionVisible(ctx, id.String(), association, supplierGroup(header))
	if err != nil {
		return err
	}
	if !found {
		return domain.ErrPersonaSubscriptionNotFound
	}
	// The viewset's delete(): the soft delete, Subscription.save() over the loaded row.
	group := supplierGroup(header)
	tx, err := s.store.(domain.PersonaSubsWriteStore).BeginPersonaSubsWrite(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	stored, err := tx.Subscription(ctx, id.String())
	if err != nil {
		return err
	}
	if stored == nil {
		return domain.ErrPersonaSubscriptionNotFound
	}
	row := *stored
	row.Deleted = true
	if err = personaSubsSeason(ctx, tx, &row); err != nil {
		return err
	}
	changes, err := personaSubsChanges(*stored, row, nil)
	if err != nil {
		return err
	}
	audit := domain.CourseSubscriptionAudit{Context: paymentCategoryAuditContext(identity, login), At: s.personas.now().UTC().Truncate(time.Microsecond), Group: group}
	if err = tx.SaveSubscription(ctx, row, changes, audit); err != nil {
		return err
	}
	return tx.Commit()
}
