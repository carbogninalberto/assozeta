package service

import (
	"testing"
	"time"
)

// emailPaymentDay is Django's payment_date.strftime('%d/%m/%Y') on the
// production image (python:3.13.16-slim-bookworm, glibc): %Y is not zero-padded
// below year 1000, so macOS's '0999' must not leak into the output.
func TestEmailRenderPaymentDayFollowsGlibcStrftime(t *testing.T) {
	for _, test := range []struct {
		name string
		paid time.Time
		want string
	}{
		{"year 999", time.Date(999, time.March, 4, 0, 0, 0, 0, time.UTC), "04/03/999"},
		{"year 9", time.Date(9, time.January, 1, 0, 0, 0, 0, time.UTC), "01/01/9"},
		{"year 2026", time.Date(2026, time.November, 15, 0, 0, 0, 0, time.UTC), "15/11/2026"},
	} {
		if got := emailPaymentDay(test.paid); got != test.want {
			t.Errorf("%s: emailPaymentDay(%s) = %q, want %q", test.name, test.paid.Format(time.RFC3339), got, test.want)
		}
	}
}
