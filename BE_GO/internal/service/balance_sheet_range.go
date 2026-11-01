package service

import "time"

// balanceSheetRange is BalanceSheetData.get_range_from_year_and_starting_date
// (now, day, month) with TIME_ZONE='UTC' and no user: the balance year that
// contains now, from its first midnight to the midnight of its last day.
// ok=false is the ValueError Python raises for a month or day out of range or
// a date datetime() rejects in now's year or the previous one.
func balanceSheetRange(now time.Time, day, month int64) (from, to time.Time, ok bool) {
	now = now.UTC()
	if month < 1 || month > 12 || day < 1 || day > 31 {
		return time.Time{}, time.Time{}, false
	}
	start := func(year int) (time.Time, bool) {
		value := time.Date(year, time.Month(month), int(day), 0, 0, 0, 0, time.UTC)
		return value, value.Day() == int(day)
	}
	current, valid := start(now.Year())
	previous, previousValid := start(now.Year() - 1)
	if !valid || !previousValid {
		return time.Time{}, time.Time{}, false
	}
	from = current
	if now.Before(current) {
		from = previous
	}
	// datetime(from.year + 1, month, day) is valid: a day that exists in
	// two consecutive years exists in every year.
	next, _ := start(from.Year() + 1)
	return from, next.AddDate(0, 0, -1), true
}
