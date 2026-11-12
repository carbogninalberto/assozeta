package domain

import (
	"context"
	"errors"
	"time"
)

var (
	// ErrCourseTagNotFound is {'msg': 'tag not found'}.
	ErrCourseTagNotFound = errors.New("course tag not found")
	// ErrCourseTagCourseNotFound is {'msg': 'course not found'}.
	ErrCourseTagCourseNotFound = errors.New("course tag course not found")
)

type CourseTagWriteTransaction interface {
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	// TagOf is CourseTags.objects.filter(tag_id=id, sport_association=association).first().
	TagOf(ctx context.Context, id, association string) (name *string, found bool, err error)
	// LiveCourse is the live, group-aware course of the association.
	LiveCourse(ctx context.Context, id, association string, group *string) (bool, error)
	CreateTag(ctx context.Context, id, name, association string, audit LoginAuditContext, at time.Time) error
	RenameTag(ctx context.Context, id, name, association string, changes map[string][2]string, audit LoginAuditContext, at time.Time) error
	DeleteTag(ctx context.Context, id string, audit LoginAuditContext, at time.Time) error
	LinkTag(ctx context.Context, tag, course string, linked bool) error
	Commit() error
	Rollback() error
}

type CourseTagWriteStore interface {
	BeginCourseTagWrite(context.Context) (CourseTagWriteTransaction, error)
}
