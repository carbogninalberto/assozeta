package domain

import (
	"errors"
	"time"
)

var ErrInvalidCalendar = errors.New("invalid calendar configuration")

// CalendarDateError retains the existing utility's deterministic validation text
// while giving services a typed error independent of HTTP transport.
type CalendarDateError struct{ Message string }

func (e *CalendarDateError) Error() string { return e.Message }
func (e *CalendarDateError) Unwrap() error { return ErrInvalidCalendar }

type CalendarRule struct {
	StartDay, StartMonth int
	CustomEnd            bool
	EndDay, EndMonth     int
}
type CalendarRange struct{ Start, End time.Time }

// CalendarDate is a PostgreSQL DATE value, with no timezone or time of day.
type CalendarDate struct{ Year, Month, Day int }
type SubscriptionDateRule struct {
	Season   CalendarRule
	Duration int
}
type SubscriptionDates struct{ Start, End *CalendarDate }
