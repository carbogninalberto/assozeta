package service

import (
	"encoding/json"
	"math"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// EvaluateScheduleDue mirrors the supported persisted ModelEntry timing subset.
// It neither reads the clock nor changes the source cursor. The caller supplies
// a trusted dispatch instant after acquiring any required database locks.
func EvaluateScheduleDue(row domain.PeriodicSchedule, now time.Time) (domain.ScheduleDue, error) {
	period, hour, minute, err := validatePeriodicDefinition(row)
	if err != nil {
		return domain.ScheduleDue{}, err
	}
	if !validScheduleTime(now) {
		return domain.ScheduleDue{}, domain.ErrScheduleInvalid
	}
	now = now.UTC()
	if !row.Enabled {
		return domain.ScheduleDue{NextCheckAfter: 5 * time.Second}, nil
	}
	last := row.DateChanged
	if row.LastRunAt != nil {
		last = *row.LastRunAt
	}
	if !validScheduleTime(last) || (row.StartTime != nil && !validScheduleTime(*row.StartTime)) {
		return domain.ScheduleDue{}, domain.ErrScheduleInvalid
	}
	last = last.UTC()
	if row.LastRunAt == nil && row.StartTime != nil {
		// Beat uses exactly 365*30 days, not thirty calendar years.
		last = last.Add(-10950 * 24 * time.Hour)
		if !validScheduleTime(last) {
			return domain.ScheduleDue{}, domain.ErrScheduleInvalid
		}
	}
	if row.StartTime != nil && now.Before(*row.StartTime) {
		start := row.StartTime.UTC()
		if row.Cron != nil {
			// Beat's due_start_time selects strictly after start, even when start
			// itself equals a cron slot. Once start is reached, this gate is bypassed.
			start = nextDailySlot(start, now, hour, minute)
		}
		delay, err := scheduleDelay(now, start)
		if err != nil {
			return domain.ScheduleDue{}, err
		}
		if remainder := delay % time.Second; remainder != 0 {
			if delay > time.Duration(math.MaxInt64)-(time.Second-remainder) {
				return domain.ScheduleDue{}, domain.ErrScheduleInvalid
			}
			delay += time.Second - remainder
		}
		return domain.ScheduleDue{NextCheckAfter: delay}, nil
	}
	var next time.Time
	if row.Interval != nil {
		next = last.Add(period)
	} else {
		next = nextDailySlot(last, now, hour, minute)
	}
	if !validScheduleTime(next) {
		return domain.ScheduleDue{}, domain.ErrScheduleInvalid
	}
	if now.Before(next) {
		delay, err := scheduleDelay(now, next)
		return domain.ScheduleDue{NextCheckAfter: delay}, err
	}
	if row.Interval != nil {
		return domain.ScheduleDue{Due: true, NextCheckAfter: period}, nil
	}
	delay, err := scheduleDelay(now, nextDailySlot(now, now, hour, minute))
	if err != nil {
		return domain.ScheduleDue{}, err
	}
	return domain.ScheduleDue{Due: true, NextCheckAfter: delay}, nil
}

func validatePeriodicDefinition(row domain.PeriodicSchedule) (time.Duration, int, int, error) {
	unsupported := func() (time.Duration, int, int, error) { return 0, 0, 0, domain.ErrScheduleUnsupported }
	if row.Task != domain.JobResetDailyEmailBalance || row.HasSolar || row.HasClocked || row.OneOff || row.ExpiresAt != nil || row.ExpireSeconds != nil ||
		row.Priority != nil || row.Queue != "" || row.Exchange != "" || row.RoutingKey != "" ||
		!emptyScheduleJSON(row.Args, true) || !emptyScheduleJSON(row.Kwargs, false) || !emptyScheduleJSON(row.Headers, false) ||
		(row.Interval == nil) == (row.Cron == nil) {
		return unsupported()
	}
	if row.Interval != nil {
		if row.Interval.Period != "seconds" || row.Interval.Every < 1 || row.Interval.Every > math.MaxInt64/int64(time.Second) {
			return unsupported()
		}
		return time.Duration(row.Interval.Every) * time.Second, 0, 0, nil
	}
	cron := row.Cron
	if cron.Timezone != "UTC" || cron.DayOfWeek != "*" || cron.DayOfMonth != "*" || cron.MonthOfYear != "*" {
		return unsupported()
	}
	hour, hourOK := scalarCronField(cron.Hour, 23)
	minute, minuteOK := scalarCronField(cron.Minute, 59)
	if !hourOK || !minuteOK {
		return unsupported()
	}
	return 0, hour, minute, nil
}

func emptyScheduleJSON(raw json.RawMessage, array bool) bool {
	// Actual ModelEntry maps empty text to [] / {}. Whitespace-only text, null,
	// wrong-shaped and nonempty values are deliberately outside this subset.
	if len(raw) == 0 {
		return true
	}
	if array {
		var value []json.RawMessage
		return json.Unmarshal(raw, &value) == nil && value != nil && len(value) == 0
	}
	var value map[string]json.RawMessage
	return json.Unmarshal(raw, &value) == nil && value != nil && len(value) == 0
}

func scalarCronField(raw string, maximum int) (int, bool) {
	if len(raw) == 0 || len(raw) > 2 {
		return 0, false
	}
	for _, character := range raw {
		if character < '0' || character > '9' {
			return 0, false
		}
	}
	value, err := strconv.Atoi(raw)
	return value, err == nil && value <= maximum
}

func nextDailySlot(after, now time.Time, hour, minute int) time.Time {
	after, now = after.UTC(), now.UTC()
	next := time.Date(after.Year(), after.Month(), after.Day(), hour, minute, 0, 0, time.UTC)
	// Match Celery remaining_delta: selecting a later minute in the same
	// hour requires the cursor date to equal now's date. A cursor on another
	// date advances a day even when its minute precedes the configured minute.
	sameDate := after.Year() == now.Year() && after.YearDay() == now.YearDay()
	if after.Hour() < hour || (sameDate && after.Hour() == hour && after.Minute() < minute) {
		return next
	}
	return next.AddDate(0, 0, 1)
}

func validScheduleTime(value time.Time) bool {
	value = value.UTC()
	return !value.IsZero() && value.Year() >= 1 && value.Year() <= 9999 && value.Nanosecond()%1000 == 0
}

func scheduleDelay(now, until time.Time) (time.Duration, error) {
	if !validScheduleTime(until) {
		return 0, domain.ErrScheduleInvalid
	}
	delay := until.Sub(now)
	if delay < 0 || !now.Add(delay).Equal(until) {
		// time.Sub saturates outside Duration's range; never publish an invented delay.
		return 0, domain.ErrScheduleInvalid
	}
	return delay, nil
}
