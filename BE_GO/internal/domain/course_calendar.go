package domain

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrCalendarCourseNotFound is the views' NotFound("Course not found.").
var ErrCalendarCourseNotFound = errors.New("Course not found.")

// ErrCalendarRaised is an exception the Django view does not handle (the 500
// page). The calendar_update POST is not atomic: work saved before the
// exception is committed; the atomic views roll back.
var ErrCalendarRaised = errors.New("calendar view raised")

// ErrCalendarNotPorted refuses, before any write, an input whose Django
// semantics are not ported (see docs/architecture/go-migration/course-calendar.md).
var ErrCalendarNotPorted = refusal.New("calendar input not ported")

// CalendarValidation is a DRF ValidationError with one string detail (a list body).
type CalendarValidation struct{ Detail string }

func (e *CalendarValidation) Error() string { return e.Detail }

// CalendarDenied is a DRF PermissionDenied raised inside a calendar view.
type CalendarDenied struct{ Detail string }

func (e *CalendarDenied) Error() string { return e.Detail }

// CalendarAnswer is a non-error response the view returns early.
type CalendarAnswer struct {
	Status int
	Body   any
}

func (e *CalendarAnswer) Error() string { return "calendar answer" }

type CalendarCourse struct {
	ID               string  `db:"course_id"`
	Title            *string `db:"title"`
	AssociationID    *string `db:"sport_association_id"`
	GoogleCalendarID *string `db:"google_calendar_id"`
}

type CalendarAssociationCard struct {
	Denomination *string `db:"denomination"`
	Logo         *string `db:"logo"`
}

type CalendarRegistry struct {
	ID       string  `db:"attendance_registry_id"`
	CourseID string  `db:"course_id"`
	Status   int     `db:"status"`
	Events   *string `db:"events"`
}

type CalendarDay struct {
	ID    string    `db:"attendance_day_id"`
	Title *string   `db:"title"`
	Date  time.Time `db:"date"`
}

type CalendarReminder struct {
	ID            string  `db:"reminders_id"`
	AssociationID *string `db:"sport_association_id"`
}

type CalendarGlobal struct {
	ID            string  `db:"global_calendar_id"`
	AssociationID *string `db:"sport_association_id"`
	Events        *string `db:"events"`
}

// CalendarLegacyDay is one Q(associated_event__isnull=True, title=..., date=...).
type CalendarLegacyDay struct {
	Title *string
	Date  time.Time
}

// CalendarRow is a model row as written: column values in SQL form.
type CalendarRow map[string]any

// CalendarAudit is one auditlog entry with its index resolution.
type CalendarAudit struct {
	Model, Key     string
	Action         int
	Changes        map[string][2]string
	Context        LoginAuditContext
	AssociationID  *string
	ResolutionPath string
	At             time.Time
}

type CalendarStore interface {
	BeginCalendar(ctx context.Context) (CalendarTransaction, error)
}

type CalendarTransaction interface {
	Commit() error
	Rollback() error
	// Attendance is the attendance helpers' view of the same transaction
	// (get_extended_prop_for_attendance_day for get_lesson).
	Attendance() CourseAttendanceTransaction
	OwnerAssociation(ctx context.Context, owner string, scope *string, lock bool) (string, error)
	ActiveAssociationOf(ctx context.Context, user string) (*string, error)
	ActiveAssociationExists(ctx context.Context, id string) (bool, error)
	Course(ctx context.Context, id string, association, group, scope *string) (*CalendarCourse, error)
	CourseByID(ctx context.Context, id string) (*CalendarCourse, error)
	AssociationCard(ctx context.Context, id string) (*CalendarAssociationCard, error)
	GoogleSyncEnabled(ctx context.Context, user string) (bool, error)
	FirstRegistry(ctx context.Context, course *string, scope *string, lock bool) (*CalendarRegistry, error)
	CourseIDs(ctx context.Context, association string, group, scope *string) ([]string, error)
	InstructorFor(ctx context.Context, user, associated string) (*string, error)
	InstructorRegistryCourses(ctx context.Context, instructor string, courses []string, scope *string) ([]string, error)
	Registries(ctx context.Context, courses []string, scope *string) ([]CalendarRegistry, error)
	DayMatch(ctx context.Context, registry string, title *string, date *time.Time, scope *string) (*string, error)
	DayByDate(ctx context.Context, registry string, date *time.Time, scope *string) (*CalendarDay, error)
	DaysByEvents(ctx context.Context, registry string, events []string, scope *string) ([]string, error)
	LegacyDays(ctx context.Context, registry string, rows []CalendarLegacyDay, scope *string) ([]string, error)
	RegistryDays(ctx context.Context, registry string) ([]string, error)
	ReminderByEvent(ctx context.Context, event string, association *string) (*CalendarReminder, error)
	RemindersByEvents(ctx context.Context, events []string, association string) ([]string, error)
	Instructor(ctx context.Context, id string, user *string) (*string, error)
	GlobalCalendars(ctx context.Context, association string) ([]CalendarGlobal, error)
	Insert(ctx context.Context, table string, row CalendarRow) error
	Update(ctx context.Context, table, key, id string, row CalendarRow) error
	Delete(ctx context.Context, table, key, id string) (map[string][2]string, error)
	Stored(ctx context.Context, table, key, id string) (map[string][2]string, error)
	AppendAudit(ctx context.Context, entry CalendarAudit) error
}
