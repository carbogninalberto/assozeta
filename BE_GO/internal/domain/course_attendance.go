package domain

import (
	"context"
	"time"
)

// AttendanceScope is scoped_queryset's boundary: the impersonation
// association and, for an athlete person inside it, that person (family
// visibility of subscriptions and course subscriptions).
type AttendanceScope struct {
	Association *string
	Athlete     *string
}

type AttendanceCourse struct {
	ID            string  `db:"course_id"`
	Title         *string `db:"title"`
	AssociationID *string `db:"sport_association_id"`
}

type AttendanceRegistryRow struct {
	ID       string  `db:"attendance_registry_id"`
	CourseID string  `db:"course_id"`
	Events   *string `db:"events"` // jsonb text
}

// AttendanceDayRow is an AttendanceDay with its registry's course (base manager).
type AttendanceDayRow struct {
	ID                string    `db:"attendance_day_id"`
	RegistryID        string    `db:"attendance_registry_id"`
	Title             *string   `db:"title"`
	Date              time.Time `db:"date"`
	Attendees         *string   `db:"attendees"`
	ExpectedAbsences  *string   `db:"expected_absences"`
	AssociatedEvent   *string   `db:"associated_event"`
	CourseID          string    `db:"course_id"`
	CourseTitle       *string   `db:"course_title"`
	CourseAssociation *string   `db:"course_association_id"`
}

type AttendanceCourseSubscription struct {
	ID             string  `db:"course_subscription_id"`
	CourseID       string  `db:"course_id"`
	CourseTitle    *string `db:"course_title"`
	SubscriptionID *string `db:"subscription_id"`
}

// AttendancePerson is a subscription with what get_full_name and
// get_medical_label read (HasAssociate=false: the associate is None).
type AttendancePerson struct {
	CourseSubscriptionPerson
	HasAssociate bool
	FirstName    *string
	LastName     *string
	TaxCode      *string
}

// AttendanceHolding is a CarnetSubscription with its payment's paid flag.
type AttendanceHolding struct {
	ID          string  `db:"carnet_subscription_id"`
	Meta        *string `db:"meta"`
	PaymentID   *string `db:"payment_id"`
	PaymentPaid *bool   `db:"payment_paid"`
	// Association is subscription.sport_association (carnet_subscription_resolver).
	Association *string `db:"subscription_association_id"`
}

type AttendanceAudit struct {
	Model, Key     string
	Changes        map[string][2]string
	Context        LoginAuditContext
	AssociationID  *string
	ResolutionPath string
	At             time.Time
}

type CourseAttendanceStore interface {
	BeginCourseAttendance(ctx context.Context) (CourseAttendanceTransaction, error)
}

type CourseAttendanceTransaction interface {
	Commit() error
	Rollback() error
	// OwnerAssociation is scoped SportAssociation.objects.get(user=owner) (ErrAssociationNotFound).
	OwnerAssociation(ctx context.Context, owner string, scope *string) (string, error)
	ActiveAssociationOf(ctx context.Context, user string) (*string, error)
	// Course is the live, group-aware course of association, within scope.
	Course(ctx context.Context, id, association string, group, scope *string) (*AttendanceCourse, error)
	// FirstRegistry is scoped AttendanceRegistry.objects.filter(course=...).first().
	FirstRegistry(ctx context.Context, course string, scope *string) (*AttendanceRegistryRow, error)
	// FirstUnlinkedDay is AttendanceDay.objects.filter(registry, associated_event=None).first().
	FirstUnlinkedDay(ctx context.Context, registry string) (*AttendanceDayRow, error)
	DayWithEventExists(ctx context.Context, registry, event string) (bool, error)
	// RegistryDays is the scoped days of a registry ordered by date.
	RegistryDays(ctx context.Context, registry string, scope *string) ([]AttendanceDayRow, error)
	// Day is the scoped day by key, of registry when given.
	// Day: owner (when given) is the user of the course's association.
	Day(ctx context.Context, id string, registry, owner *string, scope *string) (*AttendanceDayRow, error)
	// TodayDay is the first scoped day of the registry whose UTC date is date.
	TodayDay(ctx context.Context, registry, date string, scope *string) (*AttendanceDayRow, error)
	// DaysOfRegistries is the scoped days of the courses' scoped registries, newest first.
	DaysOfRegistries(ctx context.Context, courses []string, scope *string) ([]AttendanceDayRow, error)
	// CourseSubscription is the scoped course subscription by key (live only unless all).
	CourseSubscription(ctx context.Context, id string, all bool, scope AttendanceScope) (*AttendanceCourseSubscription, error)
	// CourseSubscriptionFor is the first scoped live row of course and subscription.
	CourseSubscriptionFor(ctx context.Context, course, subscription string, scope AttendanceScope) (*AttendanceCourseSubscription, error)
	// SubscriptionCourses is the scoped course subscriptions of a subscription,
	// in database order; course filters course_id.
	// SubscriptionCourses: owner (when given) is the user of the course's association.
	SubscriptionCourses(ctx context.Context, subscription, course, owner *string, all bool, scope AttendanceScope) ([]AttendanceCourseSubscription, error)
	// CourseMembers is CourseSubscription.objects.filter(course=...) in database order.
	CourseMembers(ctx context.Context, course string) ([]AttendanceCourseSubscription, error)
	Person(ctx context.Context, subscription string) (*AttendancePerson, error)
	// Subscription is the scoped, group-aware subscription by key, of
	// association and/or user when given.
	// Subscription: owner (when given) is the user of the subscription's association.
	Subscription(ctx context.Context, id string, association, owner, user, group *string, scope AttendanceScope) (*string, error)
	// InstructorName is the instructor of the owner of the course's association
	// (Instructor.objects.filter(instructor_id=id, user_id=owner).first()).
	InstructorName(ctx context.Context, id string, courseAssociation *string) (*AttendancePerson, error)
	// Holdings is the scoped carnet query of the views: the course
	// subscription's or unlinked holdings of subscription, enabled, newest first.
	// Holdings: association (when given) is the subscription's association.
	Holdings(ctx context.Context, courseSubscription string, subscription, association *string, scope *string) ([]AttendanceHolding, error)
	StoredDay(ctx context.Context, id string) (*AttendanceDayRow, error)
	StoredHoldingMeta(ctx context.Context, id string) (*string, error)
	UpdateDay(ctx context.Context, id string, attendees, absences, event *string) error
	UpdateHoldingMeta(ctx context.Context, id string, meta *string) error
	AppendAudit(ctx context.Context, entry AttendanceAudit) error
}
