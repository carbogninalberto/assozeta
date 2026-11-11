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
	// OrderTransform is the datetime extract applied to OrderColumn (creation_date__year ...).
	OrderTransform string
	// OrderSecond is the extract applied to a date or time transform
	// (creation_date__date__year, creation_date__time__hour).
	OrderSecond string
	// OrderKeys are the JSON key transforms applied to OrderColumn
	// (events__0__amount).
	OrderKeys []string
	// OrderInvalid is an ordering the compiler rejects when the rows are
	// read (a transform the field does not have).
	OrderInvalid bool
	Descending   bool
	Random       bool
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

// CourseListExportRow is one course of current_view_course's rows.
type CourseListExportRow struct {
	ID           string     `db:"course_id"`
	Title        *string    `db:"title"`
	CourseType   int64      `db:"course_type"`
	StatusFlag   int64      `db:"status_flag"`
	StartDate    *time.Time `db:"start_date"`
	EndDate      *time.Time `db:"end_date"`
	CreationDate time.Time  `db:"creation_date"`
	Pinned       bool       `db:"pinned"`
}

// CourseListExportTag is one CourseTags row; CourseID is the prefetch's
// _prefetch_related_val_course_id (empty for the association's tags).
type CourseListExportTag struct {
	CourseID      string    `db:"course_id"`
	ID            string    `db:"tag_id"`
	Name          string    `db:"tag_name"`
	AssociationID string    `db:"sport_association_id"`
	CreationDate  time.Time `db:"creation_date"`
}
