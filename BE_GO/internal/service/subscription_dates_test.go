package service

import (
	"context"
	"errors"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"reflect"
	"testing"
	"time"
)

func TestSeasonRangeExplicitContract(t *testing.T) {
	cases := []struct {
		name, at            string
		rule                domain.CalendarRule
		start, end, message string
	}{
		{"solar", "2024-06-15T12:00:00Z", domain.CalendarRule{StartDay: 1, StartMonth: 1}, "2024-01-01", "2024-12-31", ""},
		{"season before start", "2024-08-31T23:59:59.999999Z", domain.CalendarRule{StartDay: 1, StartMonth: 9}, "2023-09-01", "2024-08-31", ""},
		{"season equality", "2024-09-01T00:00:00Z", domain.CalendarRule{StartDay: 1, StartMonth: 9}, "2024-09-01", "2025-08-31", ""},
		{"positive offset crosses boundary", "2024-09-01T00:00:00+14:00", domain.CalendarRule{StartDay: 1, StartMonth: 9}, "2023-09-01", "2024-08-31", ""},
		{"negative offset crosses boundary", "2024-08-31T23:00:00-02:00", domain.CalendarRule{StartDay: 1, StartMonth: 9}, "2024-09-01", "2025-08-31", ""},
		{"custom wraps year", "2024-12-01T12:00:00Z", domain.CalendarRule{StartDay: 1, StartMonth: 11, CustomEnd: true, EndDay: 15, EndMonth: 1}, "2024-11-01", "2025-01-15", ""},
		{"custom equality", "2024-12-01T12:00:00Z", domain.CalendarRule{StartDay: 1, StartMonth: 11, CustomEnd: true, EndDay: 1, EndMonth: 11}, "2024-11-01", "2024-11-01", ""},
		{"zero custom day disables override", "2024-12-01T12:00:00Z", domain.CalendarRule{StartDay: 1, StartMonth: 11, CustomEnd: true, EndDay: 0, EndMonth: 13}, "2024-11-01", "2025-10-31", ""},
		{"disabled invalid custom values ignored", "2024-12-01T12:00:00Z", domain.CalendarRule{StartDay: 1, StartMonth: 11, CustomEnd: false, EndDay: 32, EndMonth: 13}, "2024-11-01", "2025-10-31", ""},
		{"invalid month", "2024-06-15T12:00:00Z", domain.CalendarRule{StartDay: 1, StartMonth: 13}, "", "", "Month must be between 1 and 12"},
		{"invalid day", "2024-06-15T12:00:00Z", domain.CalendarRule{StartDay: 0, StartMonth: 1}, "", "", "Day must be between 1 and 31"},
		{"invalid current date", "2023-06-15T12:00:00Z", domain.CalendarRule{StartDay: 29, StartMonth: 2}, "", "", "Invalid date combination: day is out of range for month"},
		{"leap start validates prior year", "2024-06-15T12:00:00Z", domain.CalendarRule{StartDay: 29, StartMonth: 2}, "", "", "day is out of range for month"},
		{"custom leap end validates first year", "2024-06-15T12:00:00Z", domain.CalendarRule{StartDay: 1, StartMonth: 9, CustomEnd: true, EndDay: 29, EndMonth: 2}, "", "", "day is out of range for month"},
		{"custom bad month", "2024-06-15T12:00:00Z", domain.CalendarRule{StartDay: 1, StartMonth: 1, CustomEnd: true, EndDay: 1, EndMonth: 13}, "", "", "month must be in 1..12"},
		{"year one validates prior year", "0001-06-15T12:00:00Z", domain.CalendarRule{StartDay: 1, StartMonth: 1}, "", "", "year 0 is out of range"},
		{"last year cannot construct following start", "9999-06-15T12:00:00Z", domain.CalendarRule{StartDay: 1, StartMonth: 1}, "", "", "year 10000 is out of range"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			at, e := time.Parse(time.RFC3339Nano, tc.at)
			if e != nil {
				t.Fatal(e)
			}
			got, e := SeasonRange(context.Background(), at, tc.rule)
			if tc.message != "" {
				if !errors.Is(e, domain.ErrInvalidCalendar) || e.Error() != tc.message || got != (domain.CalendarRange{}) {
					t.Fatalf("got %v/%v, want %s", got, e, tc.message)
				}
				return
			}
			if e != nil || got.Start.Format("2006-01-02") != tc.start || got.End.Format("2006-01-02") != tc.end || got.Start.Location() != time.UTC || got.End.Location() != time.UTC || got.Start.Hour() != 0 || got.End.Hour() != 0 {
				t.Fatalf("range=%v err=%v", got, e)
			}
		})
	}
}
func TestSubscriptionDateDefaultsExplicitContract(t *testing.T) {
	created := time.Date(2024, 2, 29, 23, 59, 0, 0, time.UTC)
	season := domain.CalendarRule{StartDay: 1, StartMonth: 9}
	start, end := domain.CalendarDate{Year: 1990, Month: 1, Day: 2}, domain.CalendarDate{Year: 2070, Month: 3, Day: 4}
	for _, duration := range []int{0, 1, 2, 3, 4, 5, 99} {
		for mask := 0; mask < 4; mask++ {
			input := domain.SubscriptionDates{}
			if mask&1 != 0 {
				input.Start = &start
			}
			if mask&2 != 0 {
				input.End = &end
			}
			want := input
			if want.Start == nil {
				switch duration {
				case 1, 3:
					want.Start = &domain.CalendarDate{Year: 2024, Month: 2, Day: 29}
				case 2:
					want.Start = &domain.CalendarDate{Year: 2023, Month: 9, Day: 1}
				}
			}
			if want.End == nil {
				switch duration {
				case 1:
					want.End = &domain.CalendarDate{Year: 2025, Month: 2, Day: 28}
				case 2, 3:
					want.End = &domain.CalendarDate{Year: 2024, Month: 8, Day: 31}
				}
			}
			got, e := ResolveSubscriptionDates(context.Background(), created, domain.SubscriptionDateRule{Season: season, Duration: duration}, input)
			if e != nil || !reflect.DeepEqual(got, want) {
				t.Fatalf("duration %d mask %d: %+v %v want %+v", duration, mask, got, e, want)
			}
			if got.Start != nil && got.Start == input.Start || got.End != nil && got.End == input.End {
				t.Fatal("result aliases caller-owned dates")
			}
		}
	}
	_, e := ResolveSubscriptionDates(context.Background(), created, domain.SubscriptionDateRule{Season: domain.CalendarRule{StartMonth: 0, StartDay: 1}, Duration: 99}, domain.SubscriptionDates{Start: &start, End: &end})
	if !errors.Is(e, domain.ErrInvalidCalendar) {
		t.Fatal("existing dates must not bypass calendar validation", e)
	}
	cancelled, stop := context.WithCancel(context.Background())
	stop()
	if _, e = SeasonRange(cancelled, created, season); !errors.Is(e, context.Canceled) {
		t.Fatal(e)
	}
	if _, e = ResolveSubscriptionDates(cancelled, created, domain.SubscriptionDateRule{Season: season}, domain.SubscriptionDates{}); !errors.Is(e, context.Canceled) {
		t.Fatal(e)
	}
}
