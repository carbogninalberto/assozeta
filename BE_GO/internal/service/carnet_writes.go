package service

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
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
