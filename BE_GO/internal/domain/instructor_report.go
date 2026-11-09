package domain

import (
	"context"
	"time"
)

// InstructorReportInstructor is one Instructor of the requesting user.
type InstructorReportInstructor struct {
	ID        string `db:"instructor_id"`
	FirstName string `db:"first_name"`
	LastName  string `db:"last_name"`
}

// InstructorReportRegistry is an AttendanceRegistry with its course title.
type InstructorReportRegistry struct {
	ID     string  `db:"attendance_registry_id"`
	Events *string `db:"events"`
	Course *string `db:"course_title"`
}

// InstructorReportDay is an AttendanceDay of the period.
type InstructorReportDay struct {
	Title            *string   `db:"title"`
	Date             time.Time `db:"date"`
	Attendees        *string   `db:"attendees"`
	ExpectedAbsences *string   `db:"expected_absences"`
	AssociatedEvent  *string   `db:"associated_event"`
}

type InstructorReportStore interface {
	// OwnerAssociation is request.user.sport_association for a non-athlete.
	OwnerAssociation(ctx context.Context, user string) (string, error)
	// Instructors is Instructor.objects.filter(user_id=...).
	Instructors(ctx context.Context, user string) ([]InstructorReportInstructor, error)
	// Registries is AttendanceRegistry.objects.filter(course__sport_association_id=...)
	// in the database's order (select_related('course')).
	Registries(ctx context.Context, association string) ([]InstructorReportRegistry, error)
	// Days are the registry's attendance days with date in [from, to], by date.
	Days(ctx context.Context, registry string, from, to time.Time) ([]InstructorReportDay, error)
	// Names maps the live course subscriptions among ids to
	// Concat(first_name, ' ', last_name) of their subscription's associate.
	Names(ctx context.Context, ids []string) (map[string]string, error)
}
