package service

import (
	"encoding/json"
	"math"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// EvaluateScheduleDue mirrors the supported persisted ModelEntry timing subset.
// It neither reads the clock nor changes the source cursor. The caller supplies
// a trusted dispatch instant after acquiring any required database locks.
func EvaluateScheduleDue(row domain.PeriodicSchedule, now time.Time) (domain.ScheduleDue, error) {
	period, slots, err := validatePeriodicDefinition(row)
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
			if start, err = nextCronSlot(start, now, slots); err != nil {
				return domain.ScheduleDue{}, err
			}
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
		if next, err = nextCronSlot(last, now, slots); err != nil {
			return domain.ScheduleDue{}, err
		}
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
	following, err := nextCronSlot(now, now, slots)
	if err != nil {
		return domain.ScheduleDue{}, err
	}
	delay, err := scheduleDelay(now, following)
	if err != nil {
		return domain.ScheduleDue{}, err
	}
	return domain.ScheduleDue{Due: true, NextCheckAfter: delay}, nil
}

func validatePeriodicDefinition(row domain.PeriodicSchedule) (time.Duration, cronSlots, error) {
	unsupported := func() (time.Duration, cronSlots, error) { return 0, cronSlots{}, domain.ErrScheduleUnsupported }
	if _, supported := domain.PeriodicJobKind(row.Task); !supported || row.HasSolar || row.HasClocked || row.OneOff || row.ExpiresAt != nil || row.ExpireSeconds != nil ||
		row.Priority != nil || row.Queue != "" || row.Exchange != "" || row.RoutingKey != "" ||
		!emptyScheduleJSON(row.Args, true) || !emptyScheduleJSON(row.Kwargs, false) || !emptyScheduleJSON(row.Headers, false) ||
		(row.Interval == nil) == (row.Cron == nil) {
		return unsupported()
	}
	if row.Interval != nil {
		if row.Interval.Period != "seconds" || row.Interval.Every < 1 || row.Interval.Every > math.MaxInt64/int64(time.Second) {
			return unsupported()
		}
		return time.Duration(row.Interval.Every) * time.Second, cronSlots{}, nil
	}
	cron := row.Cron
	if cron.Timezone != "UTC" || cron.DayOfWeek != "*" {
		return unsupported()
	}
	if cron.DayOfMonth != "*" || cron.MonthOfYear != "*" {
		// crontab(minute=M, hour=H, day_of_month=..., month_of_year=...) with
		// scalar hour and minute, as auto-archive-subscription declares.
		days, daysOK := cronListField(cron.DayOfMonth, 1, 31)
		months, monthsOK := cronListField(cron.MonthOfYear, 1, 12)
		hour, hourOK := scalarCronField(cron.Hour, 23)
		minute, minuteOK := scalarCronField(cron.Minute, 59)
		if !daysOK || !monthsOK || !hourOK || !minuteOK {
			return unsupported()
		}
		return 0, cronSlots{hours: []int{hour}, minutes: []int{minute}, days: days, months: months, dated: true}, nil
	}
	// crontab(minute='*') or crontab(minute='*/N') with every other field '*'
	// runs each minute or every N minutes from :00 of every hour. Other mixed
	// wildcard/scalar hour and minute forms remain outside the subset.
	if cron.Hour == "*" {
		if cron.Minute == "*" {
			return 0, cronSlots{hours: cronRange(0, 23, 1), minutes: cronRange(0, 59, 1)}, nil
		}
		step, ok := strings.CutPrefix(cron.Minute, "*/")
		if !ok {
			return unsupported()
		}
		every, ok := scalarCronField(step, 59)
		if !ok || every < 1 {
			return unsupported()
		}
		return 0, cronSlots{hours: cronRange(0, 23, 1), minutes: cronRange(0, 59, every)}, nil
	}
	hour, hourOK := scalarCronField(cron.Hour, 23)
	minute, minuteOK := scalarCronField(cron.Minute, 59)
	if !hourOK || !minuteOK {
		return unsupported()
	}
	return 0, cronSlots{hours: []int{hour}, minutes: []int{minute}}, nil
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

// cronListField expands a Celery crontab day_of_month/month_of_year field
// limited to '*', numbers and ascending a-b ranges joined by commas, as the
// sorted set crontab_parser returns. Steps, names and wrapping ranges are
// outside the subset.
func cronListField(raw string, minimum, maximum int) ([]int, bool) {
	if raw == "*" {
		return cronRange(minimum, maximum, 1), true
	}
	if raw == "" || len(raw) > 128 {
		return nil, false
	}
	seen := map[int]bool{}
	for _, part := range strings.Split(raw, ",") {
		first, last, isRange := strings.Cut(part, "-")
		low, lowOK := scalarCronField(first, maximum)
		high := low
		highOK := true
		if isRange {
			high, highOK = scalarCronField(last, maximum)
		}
		if !lowOK || !highOK || low < minimum || high < low {
			return nil, false
		}
		for value := low; value <= high; value++ {
			seen[value] = true
		}
	}
	values := []int{}
	for value := minimum; value <= maximum; value++ {
		if seen[value] {
			values = append(values, value)
		}
	}
	return values, true
}

// cronSlots are Celery's expanded crontab hour and minute sets, ascending.
// dated rows also restrict days of the month and months (ascending).
type cronSlots struct {
	hours, minutes, days, months []int
	dated                        bool
}

// onDate is Celery's execute_this_date for a wildcard day_of_week.
func (s cronSlots) onDate(value time.Time) bool {
	return !s.dated || (containsCronValue(s.months, int(value.Month())) && containsCronValue(s.days, value.Day()))
}

func cronRange(first, last, step int) []int {
	var values []int
	for value := first; value <= last; value += step {
		values = append(values, value)
	}
	return values
}

func containsCronValue(values []int, value int) bool {
	for _, candidate := range values {
		if candidate == value {
			return true
		}
	}
	return false
}

// nextCronSlot mirrors Celery crontab.remaining_delta for a wildcard
// day_of_week, with expanded hour and minute sets and, for dated rows, day of
// month and month sets (_delta_to_next).
func nextCronSlot(after, now time.Time, slots cronSlots) (time.Time, error) {
	after, now = after.UTC(), now.UTC()
	hours, minutes := slots.hours, slots.minutes
	day := time.Date(after.Year(), after.Month(), after.Day(), 0, 0, 0, 0, time.UTC)
	thisDate := slots.onDate(after)
	// Match Celery remaining_delta: selecting a later minute in the same
	// hour requires the cursor date to equal now's date and the cursor hour to
	// be in the set. A cursor on another date advances an hour or a day even
	// when its minute precedes the last one.
	sameDate := after.Year() == now.Year() && after.YearDay() == now.YearDay()
	if thisDate && sameDate && containsCronValue(hours, after.Hour()) && after.Minute() < minutes[len(minutes)-1] {
		for _, minute := range minutes {
			if minute > after.Minute() {
				return day.Add(time.Duration(after.Hour())*time.Hour + time.Duration(minute)*time.Minute), nil
			}
		}
	}
	if thisDate && after.Hour() < hours[len(hours)-1] {
		for _, hour := range hours {
			if hour > after.Hour() {
				return day.Add(time.Duration(hour)*time.Hour + time.Duration(minutes[0])*time.Minute), nil
			}
		}
	}
	if !slots.dated {
		return day.AddDate(0, 0, 1).Add(time.Duration(hours[0])*time.Hour + time.Duration(minutes[0])*time.Minute), nil
	}
	return cronDeltaToNext(after, hours[0], minutes[0], slots.days, slots.months)
}

// cronDeltaToNext is Celery crontab._delta_to_next for a wildcard
// day_of_week: from the cursor's month position, the first listed date whose
// slot is not before the cursor, rolling months and years (2000 attempts).
func cronDeltaToNext(after time.Time, hour, minute int, days, months []int) (time.Time, error) {
	year, dom, moy := after.Year(), 0, 0
	if containsCronValue(months, int(after.Month())) {
		dom = sort.SearchInts(days, after.Day()+1)
		moy = sort.SearchInts(months, int(after.Month()))
	} else {
		// bisect without a year increment, as Celery does; roll_over then
		// skips the dates before the cursor.
		moy = sort.SearchInts(months, int(after.Month())+1)
		if moy == len(months) {
			moy = 0
		}
	}
	for attempt := 0; ; attempt++ {
		if attempt == 2000 || year > 9999 {
			return time.Time{}, domain.ErrScheduleInvalid
		}
		if dom < len(days) {
			candidate := time.Date(year, time.Month(months[moy]), days[dom], hour, minute, 0, 0, time.UTC)
			if candidate.Day() == days[dom] && !candidate.Before(after) {
				return candidate, nil
			}
		}
		dom = 0
		moy++
		if moy == len(months) {
			moy = 0
			year++
		}
	}
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
