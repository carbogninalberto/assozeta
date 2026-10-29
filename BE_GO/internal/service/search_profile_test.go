package service

import (
	"testing"
	"time"
)

func TestSearchProfilePreregistration(t *testing.T) {
	day := func(text string) time.Time { value, _ := time.Parse("2006-01-02", text); return value }
	for _, tc := range []struct {
		today      string
		month, day int
		closed     bool
		remaining  int
	}{
		{"2026-08-01", 9, 1, false, 31}, {"2026-09-01", 9, 1, true, 0}, {"2026-08-31", 9, 1, false, 1},
		{"2026-05-03", 9, 1, true, 0}, {"2026-05-04", 9, 1, false, 120}, {"2026-05-05", 9, 1, false, 119},
		{"2026-01-01", 2, 31, true, 0}, {"2026-01-01", 0, 1, true, 0},
		// datetime(2027, 2, 29) raises after this year's start has passed.
		{"2028-03-01", 2, 29, true, 0}, {"2028-01-01", 2, 29, false, 59},
	} {
		closed, remaining := SearchProfilePreregistration(day(tc.today), tc.month, tc.day)
		if closed != tc.closed || remaining != tc.remaining {
			t.Errorf("%s %d/%d: got %v %d", tc.today, tc.month, tc.day, closed, remaining)
		}
	}
}
