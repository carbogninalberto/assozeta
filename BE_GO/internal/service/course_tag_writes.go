package service

import (
	"context"
	"errors"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type CourseTagWrites struct {
	store domain.CourseTagWriteStore
	now   func() time.Time
	ids   func() string
}

func NewCourseTagWrites(store domain.CourseTagWriteStore, now func() time.Time, ids func() string) *CourseTagWrites {
	return &CourseTagWrites{store: store, now: now, ids: ids}
}

var (
	// ErrCourseTagNameRequired is {'msg': 'tag_name is required'}.
	ErrCourseTagNameRequired = errors.New("tag_name is required")
	// ErrCourseTagAthlete is request.user.sport_association's PermissionDenied.
	ErrCourseTagAthlete = errors.New("User is not a sport association")
)

// courseTagName is data.get('tag_name'): a non-dict body has no get() (a
// 500); None or a missing key is the 400; the stored text is CharField's
// str() of the value (longer than 255 characters fails the column).
func courseTagName(body any) (value any, stored string, err error) {
	object, ok := body.(domain.RequestObject)
	if !ok && body != nil {
		return nil, "", ErrPaymentSaveFailed
	}
	value, _ = object.Lookup("tag_name")
	if value == nil {
		return nil, "", ErrCourseTagNameRequired
	}
	stored = dto.PythonStr(value)
	if utf8.RuneCountInString(stored) > 255 {
		return nil, "", ErrPaymentSaveFailed
	}
	return value, stored, nil
}

// begin resolves request.user.sport_association.
func (s *CourseTagWrites) begin(ctx context.Context, identity domain.ResolvedIdentity) (domain.CourseTagWriteTransaction, string, error) {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return nil, "", ErrCourseTagAthlete
	}
	tx, err := s.store.BeginCourseTagWrite(ctx)
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

func (s *CourseTagWrites) tag(ctx context.Context, tx domain.CourseTagWriteTransaction, raw, association string) (string, *string, error) {
	id, err := djangoUUIDValue(raw)
	if err != nil {
		return "", nil, err
	}
	name, found, err := tx.TagOf(ctx, id, association)
	if err == nil && !found {
		err = domain.ErrCourseTagNotFound
	}
	return id, name, err
}

// Add follows course_tags_add.
func (s *CourseTagWrites) Add(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) (map[string]any, error) {
	value, stored, err := courseTagName(body)
	if err != nil {
		return nil, err
	}
	tx, association, err := s.begin(ctx, identity)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	id := s.ids()
	if err = tx.CreateTag(ctx, id, stored, association, paymentCategoryAuditContext(identity, audit), s.now().UTC().Truncate(time.Microsecond)); err != nil {
		return nil, err
	}
	return map[string]any{"tag_id": id, "tag_name": value}, tx.Commit()
}

// Update follows course_tags_update.
func (s *CourseTagWrites) Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any,
	audit domain.LoginAuditContext) (map[string]any, error) {
	value, stored, err := courseTagName(body)
	if err != nil {
		return nil, err
	}
	tx, association, err := s.begin(ctx, identity)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	id, old, err := s.tag(ctx, tx, raw, association)
	if err != nil {
		return nil, err
	}
	changes := map[string][2]string{}
	if *old != stored {
		changes["tag_name"] = [2]string{*old, stored}
	}
	if err = tx.RenameTag(ctx, id, stored, association, changes, paymentCategoryAuditContext(identity, audit),
		s.now().UTC().Truncate(time.Microsecond)); err != nil {
		return nil, err
	}
	return map[string]any{"tag_id": id, "tag_name": value}, tx.Commit()
}

// Delete follows course_tags_delete.
func (s *CourseTagWrites) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error {
	tx, association, err := s.begin(ctx, identity)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	id, _, err := s.tag(ctx, tx, raw, association)
	if err != nil {
		return err
	}
	if err = tx.DeleteTag(ctx, id, paymentCategoryAuditContext(identity, audit), s.now().UTC().Truncate(time.Microsecond)); err != nil {
		return err
	}
	return tx.Commit()
}

// Link follows course_tags_assign (linked) and course_tags_unassign.
func (s *CourseTagWrites) Link(ctx context.Context, identity domain.ResolvedIdentity, rawTag, rawCourse, group string, linked bool) error {
	tx, association, err := s.begin(ctx, identity)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	tag, _, err := s.tag(ctx, tx, rawTag, association)
	if err != nil {
		return err
	}
	course, err := djangoUUIDValue(rawCourse)
	if err != nil {
		return err
	}
	found, err := tx.LiveCourse(ctx, course, association, supplierGroup(group))
	if err != nil {
		return err
	}
	if !found {
		return domain.ErrCourseTagCourseNotFound
	}
	if err = tx.LinkTag(ctx, tag, course, linked); err != nil {
		return err
	}
	return tx.Commit()
}
