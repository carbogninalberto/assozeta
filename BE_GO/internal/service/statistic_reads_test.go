package service

import (
	"testing"
	"time"
)

func TestBalanceSheetRange(t *testing.T) {
	at := func(text string) time.Time {
		value, err := time.Parse(time.RFC3339, text)
		if err != nil {
			t.Fatal(err)
		}
		return value
	}
	cases := []struct {
		moment     string
		day, month int
		from, to   string
	}{
		{"2025-03-10T00:00:00Z", 1, 9, "2024-09-01", "2025-08-31"},
		{"2024-09-01T00:00:00Z", 1, 9, "2024-09-01", "2025-08-31"},
		{"2024-08-31T23:59:59Z", 1, 9, "2023-09-01", "2024-08-31"},
		{"2026-11-01T21:00:00Z", 1, 1, "2026-01-01", "2026-12-31"},
		{"2026-03-01T00:00:00Z", 15, 3, "2025-03-15", "2026-03-14"},
	}
	for _, c := range cases {
		year, err := BalanceSheetRange(at(c.moment), c.day, c.month)
		if err != nil || year.From.Format("2006-01-02") != c.from || year.To.Format("2006-01-02") != c.to ||
			year.To.Hour() != 0 || year.From.Location() != time.UTC {
			t.Errorf("%v: got %v %v", c, year, err)
		}
	}
	// datetime() raises for impossible combinations, including the previous year's 29 February.
	for _, c := range []struct {
		moment     string
		day, month int
	}{
		{"2025-03-10T00:00:00Z", 30, 2}, {"2024-03-10T00:00:00Z", 29, 2}, {"2025-03-10T00:00:00Z", 1, 13}, {"2025-03-10T00:00:00Z", 0, 1},
	} {
		if _, err := BalanceSheetRange(at(c.moment), c.day, c.month); err == nil {
			t.Errorf("%v: expected ValueError", c)
		}
	}
}

func TestStatisticMonths(t *testing.T) {
	names, numbers := statisticMonths(9)
	if names[0] != "Set" || names[11] != "Ago" || numbers[0] != 9 || numbers[4] != 1 || numbers[11] != 8 {
		t.Errorf("%v %v", names, numbers)
	}
}
