package service

import (
	"context"
	"encoding/json"
	"errors"
	"math/big"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type CarnetDeletes struct {
	store domain.CarnetDeleteStore
	now   func() time.Time
}

func NewCarnetDeletes(store domain.CarnetDeleteStore, now func() time.Time) *CarnetDeletes {
	return &CarnetDeletes{store: store, now: now}
}

// Delete follows the scoped carnet_delete: association role, the owned carnet
// (a non-UUID is the ORM's ValidationError, a 500) and carnet.delete().
func (s *CarnetDeletes) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error {
	if identity.ScopeUser.Role != domain.RoleAssociation {
		return ErrCarnetNotAllowed
	}
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return ErrPaymentSaveFailed
	}
	tx, err := s.store.BeginCarnetWrite(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	association, err := tx.OwnedCarnetForUpdate(ctx, id.String(), identity.ScopeUser.ID, paymentCategoryAssociationScope(identity))
	if err != nil {
		return err
	}
	actorAssociation, err := tx.ActiveAssociationOf(ctx, identity.Actor.ID)
	if err != nil {
		return err
	}
	if err = tx.DeleteCarnet(ctx, id.String(), association, actorAssociation, paymentCategoryAuditContext(identity, audit),
		s.now().UTC().Truncate(time.Microsecond)); err != nil {
		return err
	}
	return tx.Commit()
}

type CarnetUpdates struct {
	store domain.CarnetUpdateStore
	now   func() time.Time
}

func NewCarnetUpdates(store domain.CarnetUpdateStore, now func() time.Time) *CarnetUpdates {
	return &CarnetUpdates{store: store, now: now}
}

// Update follows the scoped carnet_update: association role, serializer
// validation before the lookup, the owned carnet and its audited save.
func (s *CarnetUpdates) Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, form bool, audit domain.LoginAuditContext) error {
	if identity.ScopeUser.Role != domain.RoleAssociation {
		return ErrCarnetNotAllowed
	}
	input, err := dto.ParseCarnetUpdate(body, form)
	if err != nil {
		return err
	}
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return ErrPaymentSaveFailed
	}
	tx, err := s.store.BeginCarnetUpdate(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	row, err := tx.CarnetForEdit(ctx, id.String(), identity.ScopeUser.ID, paymentCategoryAssociationScope(identity))
	if err != nil {
		return err
	}
	changes := map[string][2]string{}
	diff := func(name, old, updated string) {
		if old != updated {
			changes[name] = [2]string{old, updated}
		}
	}
	diff("title", pythonOptional(row.Title), input.Title)
	diff("description", pythonOptional(row.Description), input.Description)
	diff("lessons_number", strconv.FormatInt(row.Lessons, 10), strconv.FormatInt(input.Lessons, 10))
	diff("fee", row.Fee, input.Fee)
	diff("public", pythonBool(row.Public), pythonBool(input.Public))
	write := domain.CarnetUpdateWrite{ID: id.String(), Title: input.Title, Description: input.Description, Fee: input.Fee, Lessons: input.Lessons,
		Public: input.Public, Changes: changes, Context: paymentCategoryAuditContext(identity, audit), At: s.now().UTC().Truncate(time.Microsecond)}
	if err = tx.UpdateCarnet(ctx, write, row.AssociationID); err != nil {
		return err
	}
	return tx.Commit()
}

type CarnetUnassigns struct {
	store domain.CarnetUnassignStore
	now   func() time.Time
}

func NewCarnetUnassigns(store domain.CarnetUnassignStore, now func() time.Time) *CarnetUnassigns {
	return &CarnetUnassigns{store: store, now: now}
}

// Unassign follows the scoped carnet_unassign: the carnet uid is unused; the
// holding must belong to the scope user's subscriptions (a non-UUID is the
// ORM's ValidationError, a 500); an unpaid payment is deleted first.
func (s *CarnetUnassigns) Unassign(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error {
	if identity.ScopeUser.Role != domain.RoleAssociation {
		return ErrCarnetNotAllowed
	}
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return ErrPaymentSaveFailed
	}
	tx, err := s.store.BeginCarnetUnassign(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	holding, err := tx.HoldingForUnassign(ctx, id.String(), identity.ScopeUser.ID, paymentCategoryAssociationScope(identity))
	if err != nil {
		return err
	}
	context := paymentCategoryAuditContext(identity, audit)
	at := s.now().UTC().Truncate(time.Microsecond)
	if holding.PaymentID != nil && holding.Paid != nil && !*holding.Paid {
		if err = tx.DeleteUnpaidPayment(ctx, *holding.PaymentID, context, at); err != nil {
			return err
		}
	}
	actorAssociation, err := tx.ActiveAssociationOf(ctx, identity.Actor.ID)
	if err != nil {
		return err
	}
	if err = tx.DeleteHolding(ctx, id.String(), actorAssociation, context, at); err != nil {
		return err
	}
	return tx.Commit()
}

type CarnetToggles struct {
	store domain.CarnetToggleStore
	now   func() time.Time
}

func NewCarnetToggles(store domain.CarnetToggleStore, now func() time.Time) *CarnetToggles {
	return &CarnetToggles{store: store, now: now}
}

// Toggle follows carnet_subscription_enable/disable.
func (s *CarnetToggles) Toggle(ctx context.Context, identity domain.ResolvedIdentity, raw string, disabled bool, audit domain.LoginAuditContext) error {
	if identity.ScopeUser.Role != domain.RoleAssociation {
		return ErrCarnetNotAllowed
	}
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return ErrPaymentSaveFailed
	}
	tx, err := s.store.BeginCarnetToggle(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	current, association, err := tx.HoldingForToggle(ctx, id.String(), identity.ScopeUser.ID, paymentCategoryAssociationScope(identity))
	if err != nil {
		return err
	}
	if err = tx.SetHoldingDisabled(ctx, id.String(), disabled, association, paymentCategoryAuditContext(identity, audit),
		s.now().UTC().Truncate(time.Microsecond), current != disabled); err != nil {
		return err
	}
	return tx.Commit()
}

type CarnetLinks struct{ store domain.CarnetLinkStore }

func NewCarnetLinks(store domain.CarnetLinkStore) *CarnetLinks { return &CarnetLinks{store: store} }

// Remove follows carnet_subscription_delete: the owned carnet subscription,
// then course_subscription.remove(uid_course) and an unchanged save.
func (s *CarnetLinks) Remove(ctx context.Context, identity domain.ResolvedIdentity, raw, rawCourse string) error {
	if identity.ScopeUser.Role != domain.RoleAssociation {
		return ErrCarnetNotAllowed
	}
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return ErrPaymentSaveFailed
	}
	tx, err := s.store.BeginCarnetLink(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, _, err = tx.HoldingForToggle(ctx, id.String(), identity.ScopeUser.ID, paymentCategoryAssociationScope(identity)); err != nil {
		return err
	}
	course, err := paymentInfoParseUUID(rawCourse)
	if err != nil {
		return ErrPaymentSaveFailed
	}
	if err = tx.RemoveCourseLink(ctx, id.String(), course.String()); err != nil {
		return err
	}
	return tx.Commit()
}

var (
	ErrCarnetLessonsMissing = errors.New("missing lessons left")
	ErrCarnetLessonsRange   = errors.New("lessons left out of range")
)

type CarnetMetas struct {
	store domain.CarnetMetaStore
	now   func() time.Time
}

func NewCarnetMetas(store domain.CarnetMetaStore, now func() time.Time) *CarnetMetas {
	return &CarnetMetas{store: store, now: now}
}

// pythonNumber compares like Python numbers: JSON numbers and booleans only.
func pythonNumber(value any) (*big.Rat, bool) {
	switch v := value.(type) {
	case bool:
		if v {
			return big.NewRat(1, 1), true
		}
		return new(big.Rat), true
	case json.Number:
		rat, ok := new(big.Rat).SetString(string(v))
		return rat, ok
	}
	return nil, false
}

// UpdateLessons follows carnet_subscription_update.
func (s *CarnetMetas) UpdateLessons(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, audit domain.LoginAuditContext) error {
	if identity.ScopeUser.Role != domain.RoleAssociation {
		return ErrCarnetNotAllowed
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		// request.data.keys() on a non-dict body.
		return ErrPaymentSaveFailed
	}
	value, present := object.Lookup("lessons_left")
	if !present {
		return ErrCarnetLessonsMissing
	}
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return err
	}
	tx, err := s.store.BeginCarnetMeta(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	stored, association, err := tx.HoldingMeta(ctx, id, identity.ScopeUser.ID, paymentCategoryAssociationScope(identity))
	if err != nil {
		return err
	}
	left, err := dto.PythonIntPrep(value)
	if err != nil {
		return ErrPaymentSaveFailed
	}
	meta, err := paymentJSON(stored)
	if err != nil {
		return ErrPaymentSaveFailed
	}
	fields, ok := meta.(domain.RequestObject)
	if !ok {
		return ErrPaymentSaveFailed
	}
	counterValue, present := fields.Lookup("lessons_counter")
	counter, numeric := pythonNumber(counterValue)
	if !present || !numeric {
		return ErrPaymentSaveFailed
	}
	if big.NewRat(left, 1).Cmp(counter) > 0 || left < 0 {
		return ErrCarnetLessonsRange
	}
	updated := make(domain.RequestObject, 0, len(fields)+1)
	replaced := false
	for _, field := range fields {
		if field.Name == "lessons_left" {
			field.Value, replaced = json.Number(strconv.FormatInt(left, 10)), true
		}
		updated = append(updated, field)
	}
	if !replaced {
		updated = append(updated, domain.RequestField{Name: "lessons_left", Value: json.Number(strconv.FormatInt(left, 10))})
	}
	oldText, newText := string(dto.PythonJSONDumpsSorted(fields)), string(dto.PythonJSONDumpsSorted(updated))
	changes := map[string][2]string{}
	if oldText != newText {
		changes["meta"] = [2]string{oldText, newText}
	}
	if err = tx.SaveHoldingMeta(ctx, id, string(dto.PythonJSONDumps(updated)), association, changes,
		paymentCategoryAuditContext(identity, audit), s.now().UTC().Truncate(time.Microsecond)); err != nil {
		return err
	}
	return tx.Commit()
}

type CarnetTopups struct {
	store domain.CarnetTopupStore
	now   func() time.Time
	ids   func() string
}

func NewCarnetTopups(store domain.CarnetTopupStore, now func() time.Time, ids func() string) *CarnetTopups {
	return &CarnetTopups{store: store, now: now, ids: ids}
}

// Topup follows carnet_subscription_topup: the uid is validated for every
// role; associations top up their subscriptions' carnets, athletes only
// carnets of subscriptions they hold (the scoped-request check has already
// bounded an impersonation session to the same queryset).
func (s *CarnetTopups) Topup(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, audit domain.LoginAuditContext) error {
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return err
	}
	tx, err := s.store.BeginCarnetTopup(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var owner *string
	if identity.ScopeUser.Role == domain.RoleAssociation {
		owner = &identity.ScopeUser.ID
	}
	source, err := tx.TopupSource(ctx, id, owner)
	if err != nil {
		return err
	}
	if owner == nil {
		if source.SubscriptionID == nil {
			// old_carnet.subscription.user on a carnet without subscription.
			return ErrPaymentSaveFailed
		}
		if source.SubscriptionUser == nil || *source.SubscriptionUser != identity.ScopeUser.ID {
			return ErrCarnetNotAllowed
		}
	}
	category, _, err := tx.DefaultPaymentCategory(ctx)
	if err != nil {
		return err
	}
	write, err := carnetHoldingWrite(ctx, tx, source, category, group, s.ids, identity, audit, s.now)
	if err != nil {
		return err
	}
	write.LinkFrom = &id
	if err = tx.CreateTopup(ctx, write); err != nil {
		return err
	}
	return tx.Commit()
}

type carnetBankAccounts interface {
	DefaultBankAccount(ctx context.Context, association *string) (*string, error)
}

// carnetHoldingWrite prepares Payment.objects.create plus
// CarnetSubscription.objects.create for a carnet's subscription holder.
func carnetHoldingWrite(ctx context.Context, tx carnetBankAccounts, source domain.CarnetTopupSource, category *string, group string,
	ids func() string, identity domain.ResolvedIdentity, audit domain.LoginAuditContext, now func() time.Time) (domain.CarnetTopupWrite, error) {
	var write domain.CarnetTopupWrite
	var groupID *string
	if group != "" {
		// UUIDField.to_python of the raw header; a rejected value is a 500.
		parsed, err := paymentInfoParseUUID(group)
		if err != nil {
			return write, ErrPaymentSaveFailed
		}
		text := parsed.String()
		groupID = &text
	}
	var account *string
	if source.SubscriptionUser != nil {
		// PaymentManager.create assigns the default account only with a user.
		var err error
		if account, err = tx.DefaultBankAccount(ctx, source.CarnetAssociation); err != nil {
			return write, err
		}
	}
	meta, err := json.Marshal(map[string]any{"lessons_counter": source.Lessons, "lessons_left": source.Lessons, "lessons_registry": []any{}})
	if err != nil {
		return write, err
	}
	return domain.CarnetTopupWrite{PaymentID: ids(), HoldingID: ids(), Source: source, CategoryID: category, Account: account, Group: groupID,
		Meta: string(meta), Context: paymentCategoryAuditContext(identity, audit), At: now().UTC().Truncate(time.Microsecond)}, nil
}

type CarnetAssigns struct {
	store domain.CarnetAssignStore
	now   func() time.Time
	ids   func() string
}

func NewCarnetAssigns(store domain.CarnetAssignStore, now func() time.Time, ids func() string) *CarnetAssigns {
	return &CarnetAssigns{store: store, now: now, ids: ids}
}

// djangoUUIDValue is UUIDField.to_python for a filter value: strings are
// parsed as hex, integers (booleans included) as uuid.UUID(int=...);
// anything else, or a rejected value, is a ValidationError (a 500).
func djangoUUIDValue(value any) (string, error) {
	switch v := value.(type) {
	case string:
		parsed, err := paymentInfoParseUUID(v)
		if err != nil {
			return "", ErrPaymentSaveFailed
		}
		return parsed.String(), nil
	case bool:
		if v {
			return "00000000-0000-0000-0000-000000000001", nil
		}
		return "00000000-0000-0000-0000-000000000000", nil
	case json.Number:
		number, ok := new(big.Int).SetString(string(v), 10)
		if !ok || number.Sign() < 0 || number.BitLen() > 128 {
			return "", ErrPaymentSaveFailed
		}
		var raw [16]byte
		number.FillBytes(raw[:])
		return uuidFromBytes(raw), nil
	}
	return "", ErrPaymentSaveFailed
}

func uuidFromBytes(raw [16]byte) string {
	const hex = "0123456789abcdef"
	buffer := make([]byte, 0, 36)
	for index, value := range raw {
		if index == 4 || index == 6 || index == 8 || index == 10 {
			buffer = append(buffer, '-')
		}
		buffer = append(buffer, hex[value>>4], hex[value&15])
	}
	return string(buffer)
}

// carnetAssignField is `name in request.data` then request.data[name]: a
// list or text body that contains the name cannot be indexed by it (a 500).
func carnetAssignField(body any, name string) (any, bool, error) {
	switch value := body.(type) {
	case nil:
		return nil, false, nil
	case domain.RequestObject:
		field, present := value.Lookup(name)
		return field, present, nil
	case []any:
		for _, item := range value {
			if item == name {
				return nil, false, ErrPaymentSaveFailed
			}
		}
		return nil, false, nil
	case string:
		if strings.Contains(value, name) {
			return nil, false, ErrPaymentSaveFailed
		}
		return nil, false, nil
	}
	return nil, false, ErrPaymentSaveFailed
}

// Assign follows carnet_assign; it returns the selected course subscription.
func (s *CarnetAssigns) Assign(ctx context.Context, identity domain.ResolvedIdentity, rawCarnet, rawSubscription, group string, body any,
	audit domain.LoginAuditContext) (*string, error) {
	tx, err := s.store.BeginCarnetAssign(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	var groupFilter *string
	if group != "" {
		value, err := djangoUUIDValue(group)
		if err != nil {
			return nil, err
		}
		groupFilter = &value
	}
	subscriptionID, err := djangoUUIDValue(rawSubscription)
	if err != nil {
		return nil, err
	}
	subscription, err := tx.AssignSubscription(ctx, subscriptionID, groupFilter)
	if err != nil {
		return nil, err
	}
	if subscription == nil {
		return nil, domain.ErrCarnetAssignSubscription
	}
	association := identity.ScopeUser.Role == domain.RoleAssociation
	if !association && (subscription.UserID == nil || *subscription.UserID != identity.ScopeUser.ID) {
		return nil, ErrCarnetNotAllowed
	}
	sportAssociation := subscription.AssociationID
	if association {
		if sportAssociation, err = tx.OwnerAssociation(ctx, identity.ScopeUser.ID, paymentCategoryAssociationScope(identity)); err != nil {
			return nil, err
		}
	}
	if sportAssociation == nil {
		return nil, domain.ErrCarnetAssignAssociation
	}
	if subscription.AssociationID == nil || *subscription.AssociationID != *sportAssociation {
		return nil, domain.ErrCarnetAssignSubscription
	}
	carnetID, err := djangoUUIDValue(rawCarnet)
	if err != nil {
		return nil, err
	}
	carnet, err := tx.AssociationCarnet(ctx, carnetID, *sportAssociation)
	if err != nil {
		return nil, err
	}
	if carnet == nil {
		return nil, domain.ErrCarnetAssignCarnet
	}
	var course *string
	value, present, err := carnetAssignField(body, "course_subscription_id")
	if err != nil {
		return nil, err
	}
	if present {
		id, err := djangoUUIDValue(value)
		if err != nil {
			return nil, err
		}
		if course, err = tx.AssociationCourseSubscription(ctx, id, *sportAssociation); err != nil {
			return nil, err
		}
	}
	value, present, err = carnetAssignField(body, "carnet_subscription_id")
	if err != nil {
		return nil, err
	}
	if present {
		holding, err := djangoUUIDValue(value)
		if err != nil {
			return nil, err
		}
		var holder *string
		if !association {
			holder = &identity.ScopeUser.ID
		}
		found, err := tx.AssignHolding(ctx, holding, *sportAssociation, holder)
		if err != nil {
			return nil, err
		}
		if !found {
			return nil, domain.ErrCarnetSubscriptionNotFound
		}
		if course == nil {
			courseValue, present, err := carnetAssignField(body, "course_id")
			if err != nil {
				return nil, err
			}
			if present {
				var courseID *string
				if courseValue != nil {
					id, err := djangoUUIDValue(courseValue)
					if err != nil {
						return nil, err
					}
					courseID = &id
				}
				if course, err = tx.SubscriptionCourse(ctx, courseID, groupFilter, subscription.ID); err != nil {
					return nil, err
				}
			}
		}
		if course == nil {
			return nil, domain.ErrCarnetAssignCourse
		}
		if err = tx.LinkCourse(ctx, holding, *course); err != nil {
			return nil, err
		}
		return course, tx.Commit()
	}
	category, _, err := tx.DefaultPaymentCategory(ctx)
	if err != nil {
		return nil, err
	}
	source := domain.CarnetTopupSource{UserID: subscription.UserID, CarnetID: carnet.ID, CarnetAssociation: sportAssociation, Fee: carnet.Fee,
		Lessons: carnet.Lessons, SubscriptionID: &subscription.ID, SubscriptionUser: subscription.UserID,
		SubscriptionAssociate: subscription.AssociateID, SubscriptionAssociation: subscription.AssociationID}
	write, err := carnetHoldingWrite(ctx, tx, source, category, group, s.ids, identity, audit, s.now)
	if err != nil {
		return nil, err
	}
	write.Link = course
	if err = tx.CreateTopup(ctx, write); err != nil {
		return nil, err
	}
	return course, tx.Commit()
}
