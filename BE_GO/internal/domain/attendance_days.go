package domain

import (
	"context"
	"errors"
	"time"
)

var ErrAttendanceDayNotFound = errors.New("error: attendance day not found.")

type AttendanceDayStore interface {
	BeginAttendanceDay(context.Context) (AttendanceDayTransaction, error)
}

type AttendanceDayTransaction interface {
	// OwnedAttendanceDay checks the day's course belongs to owner (and to scope, when set).
	OwnedAttendanceDay(ctx context.Context, id, owner string, scope *string) error
	ActiveAssociationOf(ctx context.Context, user string) (*string, error)
	DeleteAttendanceDay(ctx context.Context, id string, actorAssociation *string, audit LoginAuditContext, at time.Time) error
	Commit() error
	Rollback() error
}

type AttendanceDayLists struct {
	CourseAssociation string  `db:"course_association_id"`
	CourseID          string  `db:"course_id"`
	Attendees         *string `db:"attendees"`         // jsonb text
	ExpectedAbsences  *string `db:"expected_absences"` // jsonb text
}

type AttendanceListsAudit struct {
	DayID, AssociationID string
	Changes              map[string][2]string
	Context              LoginAuditContext
	At                   time.Time
}

type AttendanceMarkTransaction interface {
	// PrimaryAssociation is InstanceConfiguration.get_config().primary_association (nil when unset).
	PrimaryAssociation(ctx context.Context) (*string, error)
	// DayForMark locks the day when its course belongs to association (or to an
	// association owned by owner when association is nil), bounded by scope.
	DayForMark(ctx context.Context, id string, association *string, owner string, scope *string) (AttendanceDayLists, error)
	// CourseSubscriptionVisible applies the view's course-subscription filter and,
	// for an athlete person, the impersonation and athlete visibility rules.
	CourseSubscriptionVisible(ctx context.Context, id, course, association string, scope *string, athlete *string) (bool, error)
	SetAttendanceLists(ctx context.Context, id string, attendees, absences *string) error
	AppendAttendanceAudit(context.Context, AttendanceListsAudit) error
	Commit() error
	Rollback() error
}

type AttendanceMarkStore interface {
	BeginAttendanceMark(context.Context) (AttendanceMarkTransaction, error)
}
