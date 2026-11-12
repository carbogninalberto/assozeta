package service

import (
	"context"
	"fmt"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// SeasonRange preserves BalanceSheetData.get_range_from_year_and_starting_date
// under the application's configured UTC timezone. The source timestamp's civil
// year is retained, while fiscal-boundary comparisons use the actual instant.
func SeasonRange(ctx context.Context, at time.Time, rule domain.CalendarRule) (domain.CalendarRange, error) {
	empty := domain.CalendarRange{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if rule.StartMonth < 1 || rule.StartMonth > 12 {
		return empty, calendarError("Month must be between 1 and 12")
	}
	if rule.StartDay < 1 || rule.StartDay > 31 {
		return empty, calendarError("Day must be between 1 and 31")
	}
	current, err := calendarMidnight(at.Year(), rule.StartMonth, rule.StartDay)
	if err != nil {
		return empty, calendarError("Invalid date combination: " + err.Error())
	}
	// The original utility constructs this even when the current year is chosen.
	// In particular, a February 29 start also fails in a leap input year.
	previous, err := calendarMidnight(at.Year()-1, rule.StartMonth, rule.StartDay)
	if err != nil {
		return empty, err
	}
	start := current
	if at.Before(current) {
		start = previous
	}
	var end time.Time
	if rule.CustomEnd && rule.EndDay != 0 && rule.EndMonth != 0 {
		end, err = calendarMidnight(start.Year(), rule.EndMonth, rule.EndDay)
		if err != nil {
			return empty, err
		}
		if end.Before(start) {
			end, err = calendarMidnight(start.Year()+1, rule.EndMonth, rule.EndDay)
		}
	} else {
		end, err = calendarMidnight(start.Year()+1, rule.StartMonth, rule.StartDay)
		if err == nil {
			end = end.AddDate(0, 0, -1)
		}
	}
	if err != nil {
		return empty, err
	}
	return domain.CalendarRange{Start: start, End: end}, nil
}

// ResolveSubscriptionDates is the date-default portion of Subscription.save.
// It does not persist rows, audit events or job intent. Existing dates survive,
// and unhandled legacy duration modes intentionally leave missing dates nil.
func ResolveSubscriptionDates(ctx context.Context, created time.Time, rule domain.SubscriptionDateRule, existing domain.SubscriptionDates) (domain.SubscriptionDates, error) {
	period, err := SeasonRange(ctx, created, rule.Season)
	if err != nil {
		return domain.SubscriptionDates{}, err
	}
	result := domain.SubscriptionDates{Start: copyCalendarDate(existing.Start), End: copyCalendarDate(existing.End)}
	if result.Start == nil {
		switch rule.Duration {
		case 1, 3:
			result.Start = civilDate(created)
		case 2:
			result.Start = civilDate(period.Start)
		}
	}
	if result.End == nil {
		switch rule.Duration {
		case 1:
			// dateutil.relativedelta(years=1) clamps leap day; Go AddDate normalizes
			// February 29 to March 1 instead and is not equivalent here.
			year, month, day := created.Date()
			year++
			if year > 9999 {
				return domain.SubscriptionDates{}, calendarError(fmt.Sprintf("year %d is out of range", year))
			}
			last := time.Date(year, month+1, 0, 0, 0, 0, 0, time.UTC).Day()
			if day > last {
				day = last
			}
			result.End = &domain.CalendarDate{Year: year, Month: int(month), Day: day}
		case 2, 3:
			result.End = civilDate(period.End)
		}
	}
	return result, nil
}

func calendarMidnight(year, month, day int) (time.Time, error) {
	if year < 1 || year > 9999 {
		return time.Time{}, calendarError(fmt.Sprintf("year %d is out of range", year))
	}
	if month < 1 || month > 12 {
		return time.Time{}, calendarError("month must be in 1..12")
	}
	last := time.Date(year, time.Month(month)+1, 0, 0, 0, 0, 0, time.UTC).Day()
	if day < 1 || day > last {
		return time.Time{}, calendarError("day is out of range for month")
	}
	return time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC), nil
}
func calendarError(message string) error { return &domain.CalendarDateError{Message: message} }
func civilDate(value time.Time) *domain.CalendarDate {
	year, month, day := value.Date()
	return &domain.CalendarDate{Year: year, Month: int(month), Day: day}
}
func copyCalendarDate(value *domain.CalendarDate) *domain.CalendarDate {
	if value == nil {
		return nil
	}
	copy := *value
	return &copy
}
