package domain

import (
	"context"
	"errors"
	"time"
)

var (
	// ErrCourseStateNotFound is {'msg': 'Course not found.'}.
	ErrCourseStateNotFound = errors.New("course not found")
	// ErrCourseStateForeign is PermissionDenied('User not allowed.').
	ErrCourseStateForeign = errors.New("course of another association")
	// ErrCourseStateUnchanged is PermissionDenied('User cannot change the
	// status of the subscription.').
	ErrCourseStateUnchanged = errors.New("course already in the requested status")
)

// CourseState is the part of a course the enable, disable and pin views change.
type CourseState struct {
	StatusFlag    int
	Pinned        bool
	AssociationID *string
}

type CourseStateWriteTransaction interface {
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	// LiveCourse is Course.objects.filter(course_id=id).first(): the live,
	// group-aware course of any association, locked for the write.
	LiveCourse(ctx context.Context, id string, group *string) (*CourseState, error)
	// SaveCourse is Course.save() changing one field; auditlog records it.
	SaveCourse(ctx context.Context, id string, state CourseState, changes map[string][2]string, audit LoginAuditContext, at time.Time) error
	Commit() error
	Rollback() error
}

type CourseStateWriteStore interface {
	BeginCourseStateWrite(context.Context) (CourseStateWriteTransaction, error)
}
