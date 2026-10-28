package service

import (
	"context"
	"encoding/json"
	"slices"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type CarnetSubscriptionLists struct {
	store domain.CarnetSubscriptionListStore
}

func NewCarnetSubscriptionLists(store domain.CarnetSubscriptionListStore) *CarnetSubscriptionLists {
	return &CarnetSubscriptionLists{store: store}
}

// carnetLessonValue is carnet_subscription.meta[key]: a meta that is not a
// dict, or lacks the key, raises (a 500).
func carnetLessonValue(meta any, key string) (json.RawMessage, error) {
	object, ok := meta.(domain.RequestObject)
	if !ok {
		return nil, ErrPaymentSaveFailed
	}
	value, present := object.Lookup(key)
	if !present {
		return nil, ErrPaymentSaveFailed
	}
	return json.RawMessage(dto.PythonJSONDumpsCompact(value)), nil
}

// List follows carnet_subscription_list.
func (s *CarnetSubscriptionLists) List(ctx context.Context, identity domain.ResolvedIdentity, query domain.RequestObject, group string) (any, error) {
	user := identity.ScopeUser
	var scope *domain.CarnetHoldingScope
	if association := paymentCategoryAssociationScope(identity); association != nil {
		person := identity.Effective
		if person == nil {
			person = user
		}
		scope = &domain.CarnetHoldingScope{AssociationID: *association, Family: person.Role == domain.RoleAthlete}
	}
	groupFilter := supplierGroup(group)
	value, _ := query.Lookup("subscription_id")
	raw, present := value.(string)
	association := user.Role == domain.RoleAssociation
	if !association {
		ids, err := s.store.AthleteSubscriptionIDs(ctx, user.ID, scope, groupFilter)
		if err != nil {
			return nil, err
		}
		if !present || !slices.Contains(ids, raw) {
			return nil, ErrCarnetNotAllowed
		}
	}
	if !present || raw == "" {
		// carnet_subscriptions is unbound for associations without the param.
		return nil, ErrPaymentSaveFailed
	}
	id, err := djangoUUIDValue(raw)
	if err != nil {
		return nil, err
	}
	var owner *string
	if association {
		owner = &user.ID
	}
	subscription, err := s.store.ListedSubscription(ctx, id, owner, scope, groupFilter, user.ID)
	if err != nil {
		return nil, err
	}
	if subscription == nil {
		return nil, domain.ErrCarnetSubscriptionListSubscription
	}
	holdings, courses, err := s.store.ListedHoldings(ctx, *subscription, scope, groupFilter, user.ID)
	if err != nil {
		return nil, err
	}
	data := make([]map[string]any, 0, len(holdings))
	for _, holding := range holdings {
		if holding.CarnetID == nil || holding.Fee == nil {
			// carnet_subscription.carnet_id.carnet_id on a missing carnet.
			return nil, ErrPaymentSaveFailed
		}
		linked := []map[string]any{}
		for _, course := range courses {
			if course.HoldingID == holding.ID {
				linked = append(linked, map[string]any{"course_id": course.CourseID, "course_title": course.CourseTitle,
					"course_subscription_id": course.CourseSubscriptionID})
			}
		}
		var meta any
		if holding.Meta != nil {
			if meta, err = dto.ParseReferenceJSON(holding.Meta); err != nil {
				return nil, err
			}
		}
		counter, err := carnetLessonValue(meta, "lessons_counter")
		if err != nil {
			return nil, err
		}
		left, err := carnetLessonValue(meta, "lessons_left")
		if err != nil {
			return nil, err
		}
		payment := map[string]any{"payment_id": holding.PaymentID, "amount": *holding.Fee, "creation_date": nil}
		if holding.PaymentID != nil {
			payment["amount"] = *holding.PaymentAmount
			payment["creation_date"] = dto.DRFUTCDateTime(*holding.PaymentCreationDate)
		}
		data = append(data, map[string]any{"carnet_subscription_id": holding.ID, "carnet_id": *holding.CarnetID, "title": holding.Title,
			"creation_date": dto.DRFUTCDateTime(holding.CreationDate), "subscription_id": holding.SubscriptionID, "disabled": holding.Disabled,
			"payment": payment, "course": linked, "lessons_counter": counter, "lessons_left": left})
	}
	return map[string]any{"data": data}, nil
}
