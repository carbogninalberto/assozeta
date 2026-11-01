package domain

import (
	"errors"
	"time"
)

// ErrCourseListMissingAssociation is PermissionDenied('Missing
// sport_association_id query param.') for athletes.
var ErrCourseListMissingAssociation = errors.New("course list without sport_association_id")

// CourseListQuery is course_list's Course queryset: the live, group-aware
// (and, during an association-scoped session, scoped) courses of one
// association with the view's filters. Search and tag ids are already the
// values Django binds; OrderColumn is a column name the service resolved.
type CourseListQuery struct {
	AssociationID string
	Scope         *string // scoped_queryset's impersonation association
	GroupID       *string
	ActiveOnly    bool // the athlete branch's status_flag=Course.ACTIVE
	// ExcludeSubscription excludes the courses with a live course
	// subscription of this subscription.
	ExcludeSubscription *string
	Search              string
	Tags                []string
	TagsAnd             bool
	Status              *int64
	// Empty is a filter Django resolves to EmptyResultSet (an integer
	// lookup outside the column's range): no rows, no query.
	Empty       bool
	OrderColumn string // empty: -creation_date
	Descending  bool
	Random      bool
}

// CourseListValue is one values('course_id', 'title') row.
type CourseListValue struct {
	ID    string  `db:"course_id"`
	Title *string `db:"title"`
}

// CourseListAthlete is one live CourseSubscription with
// select_related('subscription__associate'); BornDate is the date's text.
type CourseListAthlete struct {
	CourseID       string    `db:"course_id"`
	Paid           bool      `db:"paid"`
	CreationDate   time.Time `db:"creation_date"`
	SubscriptionID *string   `db:"subscription_id"`
	AssociateID    *string   `db:"associate_id"`
	FirstName      *string   `db:"first_name"`
	LastName       *string   `db:"last_name"`
	BornDate       *string   `db:"born_date"`
	Sex            *string   `db:"sex"`
}
