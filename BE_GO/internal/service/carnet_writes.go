package service

import (
	"context"
	"encoding/json"
	"errors"
	"math/big"
	"strconv"
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
func (s *CarnetTopups) Topup(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error {
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
	var account *string
	if source.SubscriptionUser != nil {
		// PaymentManager.create assigns the default account only with a user.
		if account, err = tx.DefaultBankAccount(ctx, source.CarnetAssociation); err != nil {
			return err
		}
	}
	meta, err := json.Marshal(map[string]any{"lessons_counter": source.Lessons, "lessons_left": source.Lessons, "lessons_registry": []any{}})
	if err != nil {
		return err
	}
	write := domain.CarnetTopupWrite{PaymentID: s.ids(), HoldingID: s.ids(), Source: source, CategoryID: category, Account: account,
		Meta: string(meta), Context: paymentCategoryAuditContext(identity, audit), At: s.now().UTC().Truncate(time.Microsecond)}
	if err = tx.CreateTopup(ctx, id, write); err != nil {
		return err
	}
	return tx.Commit()
}
