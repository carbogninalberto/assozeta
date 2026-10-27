package service

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type AttendanceDays struct {
	store domain.AttendanceDayStore
	now   func() time.Time
}

func NewAttendanceDays(store domain.AttendanceDayStore, now func() time.Time) *AttendanceDays {
	return &AttendanceDays{store: store, now: now}
}

// Delete follows attendance_day_delete: is_valid_uuid (DRF 400), the
// course-owner lookup (a miss is the 404 for athletes too), then delete().
func (s *AttendanceDays) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return err
	}
	tx, err := s.store.BeginAttendanceDay(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = tx.OwnedAttendanceDay(ctx, id, identity.ScopeUser.ID, paymentCategoryAssociationScope(identity)); err != nil {
		return err
	}
	actorAssociation, err := tx.ActiveAssociationOf(ctx, identity.Actor.ID)
	if err != nil {
		return err
	}
	if err = tx.DeleteAttendanceDay(ctx, id, actorAssociation, paymentCategoryAuditContext(identity, audit),
		s.now().UTC().Truncate(time.Microsecond)); err != nil {
		return err
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}
