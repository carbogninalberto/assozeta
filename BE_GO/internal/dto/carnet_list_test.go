package dto

import (
	"testing"
	"time"
)

func TestSubscriptionCurrentYear(t *testing.T) {
	date := func(text string) *time.Time {
		value, _ := time.Parse("2006-01-02", text)
		return &value
	}
	today := time.Date(2026, 10, 28, 23, 59, 0, 0, time.UTC)
	for _, tc := range []struct {
		start, end *time.Time
		want       string
	}{
		{nil, date("2000-01-01"), "corrente"},
		{date("2026-10-28"), date("2026-10-28"), "corrente"},
		{date("2026-01-01"), date("2026-10-27"), "precedente"},
		{date("2026-10-29"), date("2027-01-01"), "successivo"},
		// An inverted range is neither current nor past.
		{date("2026-12-01"), date("2026-11-01"), "successivo"},
	} {
		if got := SubscriptionCurrentYear(tc.start, tc.end, today); got != tc.want {
			t.Errorf("%v-%v: got %s want %s", tc.start, tc.end, got, tc.want)
		}
	}
}
