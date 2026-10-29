package dto

import (
	"testing"
	"time"
)

func TestRelativeYearsMatchesRelativedelta(t *testing.T) {
	day := func(y, m, d int) time.Time { return time.Date(y, time.Month(m), d, 0, 0, 0, 0, time.UTC) }
	for _, tc := range []struct {
		now, born time.Time
		want      int
	}{{day(2026, 10, 27), day(2008, 10, 27), 18}, {day(2026, 10, 26), day(2008, 10, 27), 17},
		{day(2026, 2, 28), day(2008, 2, 29), 18}, {day(2026, 3, 1), day(2008, 2, 29), 18}, {day(2024, 2, 29), day(2008, 2, 29), 16}} {
		if got := RelativeYears(tc.now, tc.born); got != tc.want {
			t.Errorf("relativedelta(%s, %s).years = %d, want %d", tc.now.Format("2006-01-02"), tc.born.Format("2006-01-02"), got, tc.want)
		}
	}
}
