package service

import (
	"context"
	"errors"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type SubscriptionTags struct {
	store domain.SubscriptionTagStore
	now   func() time.Time
	ids   func() string
}

func NewSubscriptionTags(store domain.SubscriptionTagStore, now func() time.Time, ids func() string) *SubscriptionTags {
	return &SubscriptionTags{store: store, now: now, ids: ids}
}

// ErrSubscriptionTagNameRequired is TagService's ValidationError('tag_name is required').
var ErrSubscriptionTagNameRequired = errors.New("tag_name is required")

// subscriptionTagValue is request.data.get('tag_name', None): a non-dict body
// has no get() (a 500).
func subscriptionTagValue(body any) (any, error) {
	object, ok := body.(domain.RequestObject)
	if !ok && body != nil {
		return nil, ErrPaymentSaveFailed
	}
	value, _ := object.Lookup("tag_name")
	return value, nil
}

// subscriptionTagName is TagService's check (None, or a str that is empty
// after strip()) and the CharField's str() of any other value (over 255
// characters the column fails).
func subscriptionTagName(value any) (string, error) {
	if text, isString := value.(string); value == nil || isString && dto.PythonStrip(text) == "" {
		return "", ErrSubscriptionTagNameRequired
	}
	stored := dto.PythonStr(value)
	if utf8.RuneCountInString(stored) > 255 {
		return "", ErrPaymentSaveFailed
	}
	return stored, nil
}

// begin resolves request.user.sport_association: athletes are refused, a
// missing association is a 500.
func (s *SubscriptionTags) begin(ctx context.Context, identity domain.ResolvedIdentity) (domain.SubscriptionTagTransaction, string, error) {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return nil, "", &domain.PermissionDenied{Detail: "User is not a sport association"}
	}
	tx, err := s.store.BeginSubscriptionTags(ctx)
	if err != nil {
		return nil, "", err
	}
	association, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		tx.Rollback()
		return nil, "", err
	}
	return tx, association, nil
}

func (s *SubscriptionTags) tag(ctx context.Context, tx domain.SubscriptionTagTransaction, raw, association string) (string, *string, error) {
	id, err := djangoUUIDValue(raw)
	if err != nil {
		return "", nil, err
	}
	name, found, err := tx.TagOf(ctx, id, association)
	if err == nil && !found {
		err = domain.ErrSubscriptionTagNotFound
	}
	return id, name, err
}

func (s *SubscriptionTags) at() time.Time { return s.now().UTC().Truncate(time.Microsecond) }

// List follows subscription_tags_list.
func (s *SubscriptionTags) List(ctx context.Context, identity domain.ResolvedIdentity) (map[string]any, error) {
	tx, association, err := s.begin(ctx, identity)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	tags, err := tx.OrderedTags(ctx, association)
	if err != nil {
		return nil, err
	}
	return map[string]any{"tags": tags}, nil
}

// Add follows subscription_tags_add and TagService.create_tag.
func (s *SubscriptionTags) Add(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) (map[string]any, error) {
	value, err := subscriptionTagValue(body)
	if err != nil {
		return nil, err
	}
	tx, association, err := s.begin(ctx, identity)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	stored, err := subscriptionTagName(value)
	if err != nil {
		return nil, err
	}
	id := s.ids()
	if err = tx.CreateTag(ctx, id, stored, association, paymentCategoryAuditContext(identity, audit), s.at()); err != nil {
		return nil, err
	}
	return map[string]any{"tag": map[string]any{"tag_id": id, "tag_name": value}}, tx.Commit()
}

// Update follows subscription_tags_update and TagService.update_tag.
func (s *SubscriptionTags) Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any,
	audit domain.LoginAuditContext) (map[string]any, error) {
	value, err := subscriptionTagValue(body)
	if err != nil {
		return nil, err
	}
	tx, association, err := s.begin(ctx, identity)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	stored, err := subscriptionTagName(value)
	if err != nil {
		return nil, err
	}
	id, old, err := s.tag(ctx, tx, raw, association)
	if err != nil {
		return nil, err
	}
	changes := map[string][2]string{}
	if *old != stored {
		changes["tag_name"] = [2]string{*old, stored}
	}
	if err = tx.RenameTag(ctx, id, stored, association, changes, paymentCategoryAuditContext(identity, audit), s.at()); err != nil {
		return nil, err
	}
	return map[string]any{"tag_id": id, "tag_name": value}, tx.Commit()
}

// Delete follows subscription_tags_delete and TagService.delete_tag.
func (s *SubscriptionTags) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error {
	tx, association, err := s.begin(ctx, identity)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	id, _, err := s.tag(ctx, tx, raw, association)
	if err != nil {
		return err
	}
	if err = tx.DeleteTag(ctx, id, paymentCategoryAuditContext(identity, audit), s.at()); err != nil {
		return err
	}
	return tx.Commit()
}

// Link follows TagService.assign_tag_to_subscription (linked) and
// unassign_tag_from_subscription: when the link changes, Subscription.save()
// runs after the M2M write was stored, so a season range error is a 500 that
// keeps the link (requests are not atomic).
func (s *SubscriptionTags) Link(ctx context.Context, identity domain.ResolvedIdentity, rawTag, rawSubscription, group string, linked bool,
	audit domain.LoginAuditContext) error {
	tx, association, err := s.begin(ctx, identity)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	tag, _, err := s.tag(ctx, tx, rawTag, association)
	if err != nil {
		return err
	}
	subscription, err := djangoUUIDValue(rawSubscription)
	if err != nil {
		return err
	}
	target, err := tx.SubscriptionOf(ctx, subscription, association, supplierGroup(group))
	if err != nil {
		return err
	}
	changed, err := tx.SetLink(ctx, tag, subscription, linked)
	if err != nil {
		return err
	}
	if !changed {
		return tx.Commit()
	}
	dates, err := ResolveSubscriptionDates(ctx, target.Created, target.Rule, target.Dates)
	if err != nil {
		return errors.Join(err, tx.Commit())
	}
	if err = tx.SaveSubscriptionDates(ctx, subscription, association, target.Dates, dates, paymentCategoryAuditContext(identity, audit), s.at()); err != nil {
		return err
	}
	return tx.Commit()
}
