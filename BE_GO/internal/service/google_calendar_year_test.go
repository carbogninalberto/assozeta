package service

import (
	"runtime"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// An event before the year 1000 is exported as Django writes it on the same
// platform: strftime('%Y') leaves the year unpadded with glibc (the Linux
// images) and pads it on macOS.
func TestGoogleEventYearFollowsPlatform(t *testing.T) {
	want := "999"
	if runtime.GOOS == "darwin" {
		want = "0999"
	}
	if got := pythonStrftimeYear(999); got != want {
		t.Fatalf("year 999: %q, want %q", got, want)
	}
	if got := pythonStrftimeYear(2025); got != "2025" {
		t.Fatalf("year 2025: %q", got)
	}
	for _, end := range []any{"0999-01-02T11:00:00", nil} {
		body, err := googleEventBody("course", "calendar", domain.RequestObject{
			{Name: "event_id", Value: "a-b"}, {Name: "start", Value: "0999-01-02T10:00:00"},
			{Name: "end", Value: end}, {Name: "title", Value: "Lezione"},
		})
		if err != nil {
			t.Fatalf("end %v: %v", end, err)
		}
		start, _ := googleLookup(body, "start")
		field := "dateTime"
		if end == nil {
			field = "date"
		}
		// The Rome wall clock of 999 is pytz's local mean time: only the date matters here.
		got, _ := googleLookup(start.(domain.RequestObject), field)
		if text, _ := got.(string); !strings.HasPrefix(text, want+"-01-02") {
			t.Fatalf("end %v: start %v, want %s-01-02", end, got, want)
		}
	}
}
