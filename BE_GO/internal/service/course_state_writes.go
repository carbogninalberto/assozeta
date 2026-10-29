package service

import (
	"context"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

const (
	courseStatusDraft  = 1
	courseStatusActive = 2
)

type CourseStateWrites struct {
	store domain.CourseStateWriteStore
	now   func() time.Time
}

func NewCourseStateWrites(store domain.CourseStateWriteStore, now func() time.Time) *CourseStateWrites {
	return &CourseStateWrites{store: store, now: now}
}

// write follows the shared view prologue: is_valid_uuid(uid), the owner's
// association (a miss is a 500), the live group-aware course of any
// association and its association check; change edits the state and returns
// the changed field and the response message.
func (s *CourseStateWrites) write(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, audit domain.LoginAuditContext,
	change func(state *domain.CourseState) (field, before, after, msg string, err error)) (string, error) {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return "", err
	}
	tx, err := s.store.BeginCourseStateWrite(ctx)
	if err != nil {
		return "", err
	}
	defer tx.Rollback()
	association, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return "", err
	}
	state, err := tx.LiveCourse(ctx, id.String(), supplierGroup(group))
	if err != nil {
		return "", err
	}
	if state == nil {
		return "", domain.ErrCourseStateNotFound
	}
	if state.AssociationID == nil || *state.AssociationID != association {
		return "", domain.ErrCourseStateForeign
	}
	field, before, after, msg, err := change(state)
	if err != nil {
		return "", err
	}
	changes := map[string][2]string{field: {before, after}}
	if err = tx.SaveCourse(ctx, id.String(), *state, changes, paymentCategoryAuditContext(identity, audit),
		s.now().UTC().Truncate(time.Microsecond)); err != nil {
		return "", err
	}
	return msg, tx.Commit()
}

func (s *CourseStateWrites) status(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, audit domain.LoginAuditContext,
	target int, msg string) (string, error) {
	return s.write(ctx, identity, raw, group, audit, func(state *domain.CourseState) (string, string, string, string, error) {
		if state.StatusFlag == target {
			return "", "", "", "", domain.ErrCourseStateUnchanged
		}
		before := strconv.Itoa(state.StatusFlag)
		state.StatusFlag = target
		return "status_flag", before, strconv.Itoa(target), msg, nil
	})
}

// Enable follows course_enable.
func (s *CourseStateWrites) Enable(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, audit domain.LoginAuditContext) (string, error) {
	return s.status(ctx, identity, raw, group, audit, courseStatusActive, "course enabled.")
}

// Disable follows course_disable.
func (s *CourseStateWrites) Disable(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, audit domain.LoginAuditContext) (string, error) {
	return s.status(ctx, identity, raw, group, audit, courseStatusDraft, "course disabled.")
}

// Pin follows course_pin: it toggles pinned.
func (s *CourseStateWrites) Pin(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, audit domain.LoginAuditContext) (string, error) {
	return s.write(ctx, identity, raw, group, audit, func(state *domain.CourseState) (string, string, string, string, error) {
		before := pythonBool(state.Pinned)
		state.Pinned = !state.Pinned
		msg := "corso tolto dall'evidenza."
		if state.Pinned {
			msg = "corso messo in evidenza."
		}
		return "pinned", before, pythonBool(state.Pinned), msg, nil
	})
}
