package service

import (
	"testing"
	"time"
)

// TestRelativeYears pins relativedelta(now, born).years, including the clipped
// 29 February anniversary and dates in the future.
func TestRelativeYears(t *testing.T) {
	day := func(text string) time.Time {
		value, err := time.Parse("2006-01-02", text)
		if err != nil {
			t.Fatal(err)
		}
		return value
	}
	for _, item := range []struct {
		now, born string
		years     int
	}{
		{"2026-11-09", "2008-11-09", 18},
		{"2026-11-08", "2008-11-09", 17},
		{"2026-02-28", "2008-02-29", 18},
		{"2025-02-28", "2008-02-29", 17},
		{"2026-11-09", "2099-01-01", -72},
		{"2026-11-09", "2026-12-01", 0},
		{"2026-11-09", "1975-04-12", 51},
	} {
		if got := relativeYears(day(item.now), day(item.born)); got != item.years {
			t.Errorf("relativeYears(%s, %s) = %d, want %d", item.now, item.born, got, item.years)
		}
	}
}
