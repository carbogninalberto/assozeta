package domain

import "time"

// SubscriptionSaveSeason is what Subscription.save() always reads before it
// writes: the row's creation_date and its association owner's season settings
// (BalanceSheetData.get_range_from_year_and_starting_date runs on every save,
// update_fields included, and raises for settings that name no date).
type SubscriptionSaveSeason struct {
	Created time.Time
	Rule    CalendarRule
}
