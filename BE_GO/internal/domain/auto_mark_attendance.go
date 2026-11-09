package domain

import (
	"context"
	"time"

	"github.com/google/uuid"
)

const JobAutoMarkAttendance = "auto_mark_attendance"

// AutoMarkCourse is an active, not deleted course of the owner's association.
type AutoMarkCourse struct {
	ID            uuid.UUID
	Title         *string
	AssociationID uuid.UUID
}

// AutoMarkDay is an attendance day selected by the task's date range; JSON
// columns are the stored text (nil for SQL NULL).
type AutoMarkDay struct {
	ID               uuid.UUID
	Title            *string
	Date             time.Time
	Attendees        *string
	ExpectedAbsences *string
}

// AutoMarkCourseSubscription is CourseSubscription.objects.filter(pk=...).first().
type AutoMarkCourseSubscription struct {
	ID             uuid.UUID
	SubscriptionID *uuid.UUID
}

// AutoMarkCarnet is one CarnetSubscription candidate in the queryset's order.
type AutoMarkCarnet struct {
	ID             uuid.UUID
	SubscriptionID *uuid.UUID
	PaymentID      *uuid.UUID
	Meta           *string
}

type AutoMarkAttendanceStore interface {
	BeginAutoMarkAttendance(context.Context, Job) (AutoMarkAttendanceTransaction, error)
}

// AutoMarkAttendanceTransaction is one fenced READ COMMITTED transaction. The
// service reads through a long-lived one and commits each save in its own, as
// the task's autocommitted save() calls do.
type AutoMarkAttendanceTransaction interface {
	Owners(context.Context) ([]uuid.UUID, error)
	FirstAssociation(context.Context, uuid.UUID) (*uuid.UUID, error)
	ActiveCourses(context.Context, uuid.UUID) ([]AutoMarkCourse, error)
	FirstRegistry(context.Context, uuid.UUID) (*uuid.UUID, error)
	RangeDays(ctx context.Context, registry uuid.UUID, from, to time.Time) ([]AutoMarkDay, error)
	CourseSubscriptionIDs(context.Context, uuid.UUID) ([]uuid.UUID, error)
	CourseSubscription(context.Context, uuid.UUID) (*AutoMarkCourseSubscription, error)
	Carnets(ctx context.Context, courseSubscription uuid.UUID, subscription *uuid.UUID) ([]AutoMarkCarnet, error)
	PaymentPaid(context.Context, uuid.UUID) (bool, error)
	// SaveCarnetMeta writes the audit entry (when changes is not empty) and the meta.
	SaveCarnetMeta(ctx context.Context, carnet AutoMarkCarnet, meta string, changes map[string][2]string, at time.Time) error
	// SaveDay writes the audit entry (when changes is not empty) and the marked day.
	SaveDay(ctx context.Context, day AutoMarkDay, association uuid.UUID, attendees, absences string, changes map[string][2]string, at time.Time) error
	CompleteJob(context.Context, uuid.UUID, uuid.UUID) error
	Commit() error
	Rollback() error
}
