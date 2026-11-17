package config

import (
	"testing"
	"time"
)

// Django sets the process TZ to settings.TIME_ZONE ('UTC') when its settings
// load, so a naive datetime.now() or date.today() is the UTC wall clock on any
// host. Every Go command that reads the configuration runs with the same
// process zone, whatever the host's TZ.
func TestProcessTimeZoneIsDjangoTimeZone(t *testing.T) {
	if time.Local != time.UTC {
		t.Fatalf("time.Local = %v, want UTC (Django's TIME_ZONE)", time.Local)
	}
	moment := time.Date(2026, 11, 17, 23, 30, 0, 0, time.UTC)
	if got := moment.Local().Format("2006-01-02"); got != "2026-11-17" {
		t.Fatalf("local date of 23:30 UTC = %s, want 2026-11-17", got)
	}
}
