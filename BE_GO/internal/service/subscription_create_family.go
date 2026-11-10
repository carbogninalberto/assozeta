package service

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

// family follows _handle_family_subscription (is_family_wizard): one
// transaction creates a Family and runs create_subscription for every valid
// entry of multiple_entry_form_data; the answer serializes the entries'
// payments with PaymentSerializer after the commit.
func (s *SubscriptionCreates) family(ctx context.Context, identity domain.ResolvedIdentity, data domain.RequestObject,
	request SubscriptionCreateRequest) (*SubscriptionCreateResult, error) {
	server := domain.ErrSubscriptionCreateServer
	// data.get('type', 'family'): a value that is not a known type is 'family'.
	if kind, _ := data.Lookup("type"); kind == "grouped_subscriptions" {
		// The grouped registration's custom data of earlier subscriptions is not ported.
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	value, present := data.Lookup("multiple_entry_form_data")
	if !present {
		return nil, server
	}
	var entries []any
	switch v := value.(type) {
	case []any:
		entries = v
	case string:
		// Iterating a string or an object yields strings: entry['valid'] raises.
		if v != "" {
			return nil, server
		}
	case domain.RequestObject:
		if len(v) > 0 {
			return nil, server
		}
	default:
		return nil, server
	}
	tx, err := s.store.BeginSubscriptionCreate(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	keys := []string{}
	request.StorageKeys = &keys
	committed := false
	defer func() {
		if !committed {
			s.cleanupStorage(ctx, keys)
		}
	}()
	familyID := uuid.NewString()
	if err = tx.InsertFamily(ctx, familyID, "family", request.Audit, s.now().UTC()); err != nil {
		return nil, err
	}
	today := subscriptionCreateToday(s.now())
	created := []*subscriptionCreated{}
	for _, item := range entries {
		entry, isObject := item.(domain.RequestObject)
		if !isObject {
			return nil, server
		}
		valid, present := entry.Lookup("valid")
		if !present {
			return nil, server
		}
		if valid != true {
			continue
		}
		named, present := data.Lookup("sport_association")
		if !present {
			return nil, server
		}
		entry = objectSet(entry, "sport_association", named)
		associateValue, _ := entry.Lookup("associate_data")
		associate, isObject := associateValue.(domain.RequestObject)
		if !isObject {
			return nil, server
		}
		entry = objectSet(entry, "associate_data", objectSet(associate, "family", familyID))
		// {**entry['custom_data'], **asd_custom_data}: a missing key or a value that is not an object raises.
		custom, _ := entry.Lookup("custom_data")
		if _, isObject = custom.(domain.RequestObject); !isObject {
			return nil, server
		}
		if err = subscriptionCreateRefuseInputs(entry, today); err != nil {
			return nil, err
		}
		subscription, err := s.createSubscription(ctx, tx, identity, entry, request)
		if err != nil {
			return nil, err
		}
		created = append(created, subscription)
	}
	// The payments are read again after the commit and serialized as stored, in entry
	// order: a payment created by the request and a reused one have the same shape.
	payments := []map[string]any{}
	for _, subscription := range created {
		if subscription.result.PaymentID == nil {
			continue
		}
		row, err := tx.RenderPayment(ctx, *subscription.result.PaymentID)
		if err != nil {
			return nil, err
		}
		item, err := dto.PaymentStateItem(row)
		if err != nil {
			return nil, err
		}
		payments = append(payments, item)
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	committed = true
	for _, subscription := range created {
		s.afterCommit(ctx, identity, subscription, request)
	}
	return &SubscriptionCreateResult{Family: true, Payments: payments}, nil
}
