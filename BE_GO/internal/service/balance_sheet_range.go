package service

import "time"

// balanceSheetRange is BalanceSheetRange for callers that only need the
// bounds: ok=false is the ValueError Python raises.
func balanceSheetRange(now time.Time, day, month int64) (from, to time.Time, ok bool) {
	if day < 1 || day > 31 || month < 1 || month > 12 {
		return time.Time{}, time.Time{}, false
	}
	year, err := BalanceSheetRange(now, int(day), int(month))
	return year.From, year.To, err == nil
}
