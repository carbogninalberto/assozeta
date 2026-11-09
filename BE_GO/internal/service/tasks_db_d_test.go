package service

import (
	"regexp"
	"testing"
	"time"
)

func TestTasksDBDAddMonthsIsRelativedelta(t *testing.T) {
	// datetime + relativedelta(months=n) in dateutil 2.9.
	for _, item := range []struct {
		at     string
		months int
		want   string
	}{
		{"2026-01-31T10:30:00Z", 1, "2026-02-28T10:30:00Z"},
		{"2024-02-29T00:00:00Z", 12, "2025-02-28T00:00:00Z"},
		{"2025-11-30T23:15:00Z", 3, "2026-02-28T23:15:00Z"},
		{"2024-01-31T08:00:00.123456Z", 1, "2024-02-29T08:00:00.123456Z"},
		{"2025-12-15T00:00:00Z", 2, "2026-02-15T00:00:00Z"},
		{"2025-05-31T00:00:00Z", 0, "2025-05-31T00:00:00Z"},
	} {
		at, _ := time.Parse(time.RFC3339Nano, item.at)
		if got := tasksDBDAddMonths(at, item.months).Format(time.RFC3339Nano); got != item.want {
			t.Fatalf("%s + %d months = %s, want %s", item.at, item.months, got, item.want)
		}
	}
}

func TestTasksDBDIsoformatAndReadableCode(t *testing.T) {
	at := time.Date(2026, 11, 9, 10, 13, 25, 476661000, time.UTC)
	if got := tasksDBDIsoformat(at); got != "2026-11-09T10:13:25.476661+00:00" {
		t.Fatal(got)
	}
	if got := tasksDBDIsoformat(at.Truncate(time.Second)); got != "2026-11-09T10:13:25+00:00" {
		t.Fatal(got)
	}
	shape := regexp.MustCompile(`\A[A-HJKMNP-Z2-9]{8}\z`)
	for range 200 {
		code, err := tasksDBDReadableCode()
		if err != nil || !shape.MatchString(code) {
			t.Fatal(code, err)
		}
	}
}
