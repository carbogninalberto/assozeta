//go:build handoff

// Phase 4 (test_p4_scheduling, contract Part A): the native evaluator decides every
// authored Beat case of TESTS/fixtures/scheduler-authority.json the way Django's
// ModelEntry does, not only the UTC subset pinned by TestScheduleDuePortableContract:
// local-time crontabs across DST (gap and fold), day_of_week, clocked one-offs,
// completed one-offs and absolute/relative expiry. Driven by TESTS/handoff/test_p4_go_units.py.
//
// Fields the evaluator needs beyond today's domain.PeriodicSchedule are pinned by name and
// set by reflection so this file compiles before they exist:
//   ClockedTime   *time.Time  (django_celery_beat_clockedschedule.clocked_time)
//   TotalRunCount int64       (django_celery_beat_periodictask.total_run_count)
package service

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"slices"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type p4DueInput struct {
	Kind          string  `json:"kind"`
	Every         int64   `json:"every"`
	Minute        string  `json:"minute"`
	Hour          string  `json:"hour"`
	DayOfWeek     string  `json:"day_of_week"`
	DayOfMonth    string  `json:"day_of_month"`
	MonthOfYear   string  `json:"month_of_year"`
	Timezone      string  `json:"timezone"`
	Clocked       *string `json:"clocked"`
	OneOff        bool    `json:"one_off"`
	Count         int64   `json:"count"`
	Expires       *string `json:"expires"`
	ExpireSeconds *int    `json:"expire_seconds"`
	Last          *string `json:"last"`
	DateChanged   string  `json:"date_changed"`
	Start         *string `json:"start"`
	Now           string  `json:"now"`
	Enabled       *bool   `json:"enabled"`
}

func p4SetField(t *testing.T, row *domain.PeriodicSchedule, name string, value any) {
	t.Helper()
	field := reflect.ValueOf(row).Elem().FieldByName(name)
	if !field.IsValid() || !field.CanSet() {
		t.Fatalf("contract: domain.PeriodicSchedule.%s is missing (Phase 4 evaluator input)", name)
	}
	given := reflect.ValueOf(value)
	if !given.Type().AssignableTo(field.Type()) {
		t.Fatalf("contract: domain.PeriodicSchedule.%s must be %s", name, given.Type())
	}
	field.Set(given)
}

func TestP4ScheduleDueMatchesEveryAuthoredBeatCase(t *testing.T) {
	data, err := os.ReadFile(filepath.Join("..", "..", "..", "TESTS", "fixtures", "scheduler-authority.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Cases map[string]struct {
			Input    *p4DueInput    `json:"input"`
			Expected dueExpectation `json:"expected"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	names := make([]string, 0, len(fixture.Cases))
	for name, item := range fixture.Cases {
		if item.Input != nil && !slices.Contains(scheduleSubset, name) {
			names = append(names, name)
		}
	}
	slices.Sort(names)
	if len(names) < 12 {
		t.Fatalf("expected the non-UTC/one-off/expiry Beat cases, found %v", names)
	}
	for _, name := range names {
		item := fixture.Cases[name]
		t.Run(name, func(t *testing.T) {
			input := item.Input
			row := basicSchedule()
			if input.Enabled != nil {
				row.Enabled = *input.Enabled
			}
			if input.Last != nil {
				value := scheduleTime(t, *input.Last)
				row.LastRunAt = &value
			}
			if input.DateChanged != "" {
				row.DateChanged = scheduleTime(t, input.DateChanged)
			}
			if input.Start != nil {
				value := scheduleTime(t, *input.Start)
				row.StartTime = &value
			}
			if input.Expires != nil {
				value := scheduleTime(t, *input.Expires)
				row.ExpiresAt = &value
			}
			row.ExpireSeconds = input.ExpireSeconds
			row.OneOff = input.OneOff
			switch input.Kind {
			case "interval":
				row.Interval.Every = input.Every
			case "cron":
				row.Interval = nil
				row.Cron = &domain.PeriodicCron{Minute: input.Minute, Hour: "*", DayOfWeek: "*", DayOfMonth: "*",
					MonthOfYear: "*", Timezone: "UTC"}
				for target, value := range map[*string]string{&row.Cron.Hour: input.Hour, &row.Cron.DayOfWeek: input.DayOfWeek,
					&row.Cron.DayOfMonth: input.DayOfMonth, &row.Cron.MonthOfYear: input.MonthOfYear, &row.Cron.Timezone: input.Timezone} {
					if value != "" {
						*target = value
					}
				}
			case "clocked":
				row.Interval = nil
				row.HasClocked = true
				value := scheduleTime(t, *input.Clocked)
				p4SetField(t, &row, "ClockedTime", &value)
			default:
				t.Fatalf("unknown fixture kind %q", input.Kind)
			}
			if input.Count != 0 {
				p4SetField(t, &row, "TotalRunCount", input.Count)
			}
			actual, err := EvaluateScheduleDue(row, scheduleTime(t, input.Now))
			if err != nil {
				t.Fatalf("Beat decides this persisted definition (due=%v next=%vs); the Go evaluator refused it: %v",
					item.Expected.Due, item.Expected.Next, err)
			}
			if actual.Due != item.Expected.Due || actual.NextCheckAfter != time.Duration(item.Expected.Next*float64(time.Second)) {
				t.Fatalf("Beat: due=%v next=%vs; Go: due=%v next=%v", item.Expected.Due, item.Expected.Next,
					actual.Due, actual.NextCheckAfter)
			}
		})
	}
}
