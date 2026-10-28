package service

import (
	"context"
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
