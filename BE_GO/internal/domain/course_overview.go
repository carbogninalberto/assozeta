package domain

import (
	"errors"
	"time"
)

// ErrCourseOverviewNotFound is PermissionDenied('Course not found').
var ErrCourseOverviewNotFound = errors.New("course overview not found")

// CourseOverviewRow is one application_course row with its tags and
// locations as JSON arrays; decimals are numeric text and JSON fields jsonb text.
type CourseOverviewRow struct {
	ID               string     `db:"course_id"`
	StatusFlag       int        `db:"status_flag"`
	CourseType       int        `db:"course_type"`
	CreationDate     time.Time  `db:"creation_date"`
	Title            *string    `db:"title"`
	Description      *string    `db:"description"`
	MultiPayments    bool       `db:"multi_payments"`
	Fee              *string    `db:"fee"`
	Events           *string    `db:"events"`
	OneFeePayment    bool       `db:"one_fee_payment"`
	OneFee           string     `db:"one_fee"`
	GoogleCalendarID *string    `db:"google_calendar_id"`
	Color            *string    `db:"google_background_color"`
	MultipleQuotes   *string    `db:"multiple_quotes"`
	StartDate        *time.Time `db:"start_date"`
	EndDate          *time.Time `db:"end_date"`
	Pinned           bool       `db:"pinned"`
	SportSeason      bool       `db:"billed_duration_is_sport_season"`
	BilledFrequency  *int       `db:"billed_frequency"`
	FromSubscription bool       `db:"billed_from_subscription_date"`
	BilledDay        int        `db:"billed_from_day_of_month"`
	AutoRenewal      bool       `db:"auto_renewal"`
	Deleted          bool       `db:"deleted"`
	AssociationID    *string    `db:"sport_association_id"`
	GroupID          *string    `db:"group_id"`
	Tags             string     `db:"tags"`
	Locations        string     `db:"locations"`
}
