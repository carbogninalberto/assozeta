package service

import (
	"errors"
	"testing"
	"time"
)

func TestInstructorWindow(t *testing.T) {
	now := time.Date(2026, 2, 14, 23, 30, 0, 0, time.UTC)
	day := func(text string) string { return text }
	for _, tc := range []struct {
		raw      string
		present  bool
		from, to string
		err      bool
	}{
		{"", false, "2026-02-01", "2026-02-28", false},
		{"2026/9/1 al 2026/09/30", true, "2026-09-01", "2026-09-30", false},
		{"01/10/2025 al 31/10/2025", true, "2025-10-01", "2025-10-31", false},
		// Mixed formats and impossible dates fall back to the last year.
		{"2026/09/01 al 30/09/2026", true, "2025-02-14", "2026-02-15", false},
		{"2026/02/30 al 2026/03/01", true, "2025-02-14", "2026-02-15", false},
		{"2026/09/01", true, "", "", true},
		{"", true, "", "", true},
		{"a al b al c", true, "", "", true},
	} {
		from, to, err := InstructorWindow(now, tc.raw, tc.present)
		if tc.err != errors.Is(err, ErrInstructorRange) || (!tc.err && (from.Format("2006-01-02") != day(tc.from) || to.Format("2006-01-02") != tc.to)) {
			t.Errorf("%q: got %v %v %v", tc.raw, from, to, err)
		}
	}
}
