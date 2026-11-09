package domain

import (
	"context"
	"errors"
	"time"
)

// GoogleUser is the part of a User the google/* views read and save.
// Credentials is the stored integration_google_credentials JSON text (nil for
// SQL NULL).
type GoogleUser struct {
	ID          string  `db:"user_id"`
	FirstName   string  `db:"first_name"`
	LastName    string  `db:"last_name"`
	Role        int     `db:"role"`
	Deleted     bool    `db:"deleted"`
	State       *string `db:"integration_google_state"`
	Credentials *string `db:"integration_google_credentials"`
	AutoSync    bool    `db:"integration_google_auto_sync"`
}

// GoogleUserFields are the values a User.save() writes for the Google
// integration; Credentials is the Python value (nil or a str) as JSON text
// of that value (nil for None).
type GoogleUserFields struct {
	State       *string
	Credentials *string
	AutoSync    bool
}

// GoogleCourse is the Course row export_course reads and saves.
type GoogleCourse struct {
	ID          string  `db:"course_id"`
	Title       *string `db:"title"`
	Association *string `db:"sport_association_id"`
	CalendarID  *string `db:"google_calendar_id"`
	Color       *string `db:"google_background_color"`
}

// GoogleRegistry is the first AttendanceRegistry of a course; Events is the
// stored JSON text (nil for SQL NULL).
type GoogleRegistry struct {
	ID     string  `db:"attendance_registry_id"`
	Events *string `db:"events"`
}

// GoogleCalendarStore persists the google/* views' reads and saves. Every
// save is User.save() / Course.save() on its own (requests are not atomic),
// audited with only the changed fields.
type GoogleCalendarStore interface {
	User(ctx context.Context, id string) (*GoogleUser, error)
	UserByState(ctx context.Context, state string) (*GoogleUser, error)
	SaveUser(ctx context.Context, id string, fields GoogleUserFields, audit LoginAuditContext, at time.Time) error
	Course(ctx context.Context, id string, group *string) (*GoogleCourse, error)
	SaveCourse(ctx context.Context, course GoogleCourse, audit LoginAuditContext, at time.Time) error
	FirstRegistry(ctx context.Context, course string) (*GoogleRegistry, error)
	// CreateSession is SessionStore.create() of the cache backend for {'state': state}: the new session key.
	CreateSession(ctx context.Context, state string) (string, error)
}

var (
	ErrGoogleCourseNotFound   = errors.New("Course not found.")
	ErrGoogleRegistryNotFound = errors.New("Attendance Registry not found.")
	ErrGoogleNoUser           = errors.New("No user found for the integration flow.")
	ErrGoogleQuotaExceeded    = errors.New("quotaExceeded")
	ErrGoogleCreateCalendar   = errors.New("Error creating calendar.")
	// ErrGoogleServer is an exception the view does not handle (the 500 page).
	ErrGoogleServer = errors.New("google calendar view exception")
)
