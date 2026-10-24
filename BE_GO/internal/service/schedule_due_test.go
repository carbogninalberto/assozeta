package service

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

var scheduleSubset = []string{
	"interval_wait", "interval_missed_slots", "disabled", "future_start_ceiling",
	"utc_daily_before_boundary", "utc_daily_at_boundary", "utc_daily_after_boundary",
	"utc_daily_already_dispatched", "interval_future_cursor", "utc_daily_future_cursor",
	"utc_daily_start_before_slot", "utc_daily_start_at_slot", "utc_daily_start_reached",
	"utc_daily_previous_date_same_hour", "utc_daily_future_date_same_hour",
	"utc_daily_same_date_before_minute", "utc_daily_previous_date_at_due_minute",
	"utc_daily_start_future_date_same_hour", "utc_daily_start_same_date_same_hour",
	"interval_initial_cursor", "interval_initial_started", "utc_daily_initial_before_slot", "utc_daily_initial_at_slot",
}

type dueFixtureInput struct {
	Kind        string  `json:"kind"`
	Every       int64   `json:"every"`
	Minute      string  `json:"minute"`
	Hour        string  `json:"hour"`
	Last        *string `json:"last"`
	DateChanged string  `json:"date_changed"`
	Start       *string `json:"start"`
	Now         string  `json:"now"`
	Enabled     *bool   `json:"enabled"`
}

type dueExpectation struct {
	Due     bool    `json:"due"`
	Next    float64 `json:"next_seconds"`
	Enabled bool    `json:"enabled"`
	Count   int     `json:"count"`
}

func scheduleTime(t *testing.T, raw string) time.Time {
	t.Helper()
	parsed, err := time.Parse(time.RFC3339Nano, raw)
	if err != nil {
		t.Fatal(err)
	}
	return parsed
}

func basicSchedule() domain.PeriodicSchedule {
	return domain.PeriodicSchedule{
		Task: domain.JobResetDailyEmailBalance, Enabled: true,
		Interval:    &domain.PeriodicInterval{Every: 60, Period: "seconds"},
		DateChanged: time.Date(2026, 10, 22, 11, 59, 0, 0, time.UTC),
		Args:        json.RawMessage(`[]`), Kwargs: json.RawMessage(`{}`), Headers: json.RawMessage(`{}`),
	}
}

func TestScheduleDuePortableContract(t *testing.T) {
	fixturePath := filepath.Join("..", "..", "..", "TESTS", "fixtures", "scheduler-authority.json")
	data, err := os.ReadFile(fixturePath)
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Cases map[string]struct {
			Input    dueFixtureInput `json:"input"`
			Expected dueExpectation  `json:"expected"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	// The differential adapter requires this actual Django report. Ordinary unit
	// runs still use the shared authored expectations without requiring a database.
	var observed map[string]dueExpectation
	if reportPath := os.Getenv("TESTS_SCHEDULER_AUTHORITY_REPORT"); reportPath != "" {
		raw, err := os.ReadFile(reportPath)
		if err != nil {
			t.Fatal(err)
		}
		var report struct {
			Success bool                       `json:"success"`
			Cases   map[string]json.RawMessage `json:"cases"`
		}
		if err := json.Unmarshal(raw, &report); err != nil {
			t.Fatal(err)
		}
		if !report.Success {
			t.Fatal("actual Django oracle did not finish successfully")
		}
		observed = make(map[string]dueExpectation)
		for _, name := range scheduleSubset {
			var value dueExpectation
			if err := json.Unmarshal(report.Cases[name], &value); err != nil {
				t.Fatal(err)
			}
			observed[name] = value
		}
	}
	for _, name := range scheduleSubset {
		t.Run(name, func(t *testing.T) {
			item, exists := fixture.Cases[name]
			if !exists {
				t.Fatal("missing authored case")
			}
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
			if input.Kind == "interval" {
				row.Interval.Every = input.Every
			} else {
				row.Interval = nil
				row.Cron = &domain.PeriodicCron{Minute: input.Minute, Hour: input.Hour,
					DayOfWeek: "*", DayOfMonth: "*", MonthOfYear: "*", Timezone: "UTC"}
			}
			before, _ := json.Marshal(row)
			actual, err := EvaluateScheduleDue(row, scheduleTime(t, input.Now))
			if err != nil {
				t.Fatal(err)
			}
			if actual.Due != item.Expected.Due || actual.NextCheckAfter.Seconds() != item.Expected.Next {
				t.Fatalf("expected due=%v next=%v, observed %+v", item.Expected.Due, item.Expected.Next, actual)
			}
			after, _ := json.Marshal(row)
			if string(before) != string(after) {
				t.Fatal("pure evaluation mutated the source snapshot")
			}
			if observed != nil && !reflect.DeepEqual(observed[name], item.Expected) {
				t.Fatalf("actual Django result differs from authored expectation: %+v", observed[name])
			}
		})
	}
}

func TestScheduleDueRejectsUnsupportedInputs(t *testing.T) {
	now := time.Date(2026, 10, 22, 12, 0, 0, 0, time.UTC)
	integer := 0
	cases := map[string]func(*domain.PeriodicSchedule){
		"other task":                func(s *domain.PeriodicSchedule) { s.Task = "send_reminders" },
		"unregistered alias":        func(s *domain.PeriodicSchedule) { s.Task = "application.tasks.reset_communication_daily_email_balance" },
		"one off":                   func(s *domain.PeriodicSchedule) { s.OneOff = true },
		"absolute expiry":           func(s *domain.PeriodicSchedule) { s.ExpiresAt = &now },
		"relative expiry even zero": func(s *domain.PeriodicSchedule) { s.ExpireSeconds = &integer },
		"explicit priority":         func(s *domain.PeriodicSchedule) { s.Priority = &integer },
		"queue":                     func(s *domain.PeriodicSchedule) { s.Queue = "custom" },
		"exchange":                  func(s *domain.PeriodicSchedule) { s.Exchange = "custom" },
		"routing":                   func(s *domain.PeriodicSchedule) { s.RoutingKey = "custom" },
		"nonempty args":             func(s *domain.PeriodicSchedule) { s.Args = json.RawMessage(`[1]`) },
		"nonempty kwargs":           func(s *domain.PeriodicSchedule) { s.Kwargs = json.RawMessage(`{"flag":true}`) },
		"nonempty headers":          func(s *domain.PeriodicSchedule) { s.Headers = json.RawMessage(`{"trace":"value"}`) },
		"null args":                 func(s *domain.PeriodicSchedule) { s.Args = json.RawMessage(`null`) },
		"wrong kwargs type":         func(s *domain.PeriodicSchedule) { s.Kwargs = json.RawMessage(`[]`) },
		"malformed args":            func(s *domain.PeriodicSchedule) { s.Args = json.RawMessage(`{`) },
		"whitespace only headers":   func(s *domain.PeriodicSchedule) { s.Headers = json.RawMessage(` `) },
		"trailing JSON":             func(s *domain.PeriodicSchedule) { s.Kwargs = json.RawMessage(`{} {}`) },
		"no definition":             func(s *domain.PeriodicSchedule) { s.Interval = nil },
		"solar also present":        func(s *domain.PeriodicSchedule) { s.HasSolar = true },
		"clocked also present":      func(s *domain.PeriodicSchedule) { s.HasClocked = true },
		"two definitions":           func(s *domain.PeriodicSchedule) { s.Cron = &domain.PeriodicCron{} },
		"minutes interval":          func(s *domain.PeriodicSchedule) { s.Interval.Period = "minutes" },
		"zero interval":             func(s *domain.PeriodicSchedule) { s.Interval.Every = 0 },
		"negative interval":         func(s *domain.PeriodicSchedule) { s.Interval.Every = -1 },
		"overflow interval":         func(s *domain.PeriodicSchedule) { s.Interval.Every = 1<<63 - 1 },
	}
	for name, mutate := range cases {
		t.Run(name, func(t *testing.T) {
			row := basicSchedule()
			mutate(&row)
			result, err := EvaluateScheduleDue(row, now)
			if !errors.Is(err, domain.ErrScheduleUnsupported) || result != (domain.ScheduleDue{}) {
				t.Fatalf("unsupported row must return no decision: %+v %v", result, err)
			}
		})
	}
	cronCases := map[string]func(*domain.PeriodicCron){
		"timezone":        func(c *domain.PeriodicCron) { c.Timezone = "Europe/Rome" },
		"minute steps":    func(c *domain.PeriodicCron) { c.Minute = "*/15" },
		"minute wildcard": func(c *domain.PeriodicCron) { c.Minute = "*" },
		"hour list":       func(c *domain.PeriodicCron) { c.Hour = "0,12" },
		"minute range":    func(c *domain.PeriodicCron) { c.Minute = "0-10" },
		"invalid minute":  func(c *domain.PeriodicCron) { c.Minute = "60" },
		"invalid hour":    func(c *domain.PeriodicCron) { c.Hour = "24" },
		"signed hour":     func(c *domain.PeriodicCron) { c.Hour = "+1" },
		"whitespace hour": func(c *domain.PeriodicCron) { c.Hour = " 1" },
		"weekday":         func(c *domain.PeriodicCron) { c.DayOfWeek = "1" },
		"monthday":        func(c *domain.PeriodicCron) { c.DayOfMonth = "1" },
		"month":           func(c *domain.PeriodicCron) { c.MonthOfYear = "1" },
	}
	for name, mutate := range cronCases {
		t.Run("cron "+name, func(t *testing.T) {
			row := basicSchedule()
			row.Interval = nil
			row.Cron = &domain.PeriodicCron{Minute: "0", Hour: "0", DayOfWeek: "*", DayOfMonth: "*", MonthOfYear: "*", Timezone: "UTC"}
			mutate(row.Cron)
			result, err := EvaluateScheduleDue(row, now)
			if !errors.Is(err, domain.ErrScheduleUnsupported) || result != (domain.ScheduleDue{}) {
				t.Fatalf("%+v %v", result, err)
			}
		})
	}
}

func TestScheduleDueEmptyDefaultsAndInvalidClock(t *testing.T) {
	now := time.Date(2026, 10, 22, 12, 0, 0, 0, time.UTC)
	row := basicSchedule()
	row.Args = nil
	row.Kwargs = nil
	row.Headers = nil
	actual, err := EvaluateScheduleDue(row, now)
	if err != nil || !actual.Due || actual.NextCheckAfter != time.Minute {
		t.Fatalf("blank model defaults: %+v %v", actual, err)
	}
	for _, mutate := range []func(*domain.PeriodicSchedule){
		func(s *domain.PeriodicSchedule) { s.DateChanged = time.Time{} },
		func(s *domain.PeriodicSchedule) { value := time.Time{}; s.LastRunAt = &value },
		func(s *domain.PeriodicSchedule) { value := time.Time{}; s.StartTime = &value },
	} {
		row := basicSchedule()
		mutate(&row)
		if _, err := EvaluateScheduleDue(row, now); !errors.Is(err, domain.ErrScheduleInvalid) {
			t.Fatalf("invalid cursor accepted: %v", err)
		}
	}
	if _, err := EvaluateScheduleDue(basicSchedule(), time.Time{}); !errors.Is(err, domain.ErrScheduleInvalid) {
		t.Fatalf("zero clock: %v", err)
	}
}

func TestScheduleDueRejectsUnrepresentableTime(t *testing.T) {
	cases := []struct {
		name    string
		now     time.Time
		prepare func(*domain.PeriodicSchedule)
	}{
		{"UTC conversion exceeds Python datetime", time.Date(9999, 12, 31, 23, 30, 0, 0, time.FixedZone("fixture", -3600)), func(s *domain.PeriodicSchedule) { s.Enabled = false }},
		{"nanosecond clock exceeds PostgreSQL precision", time.Date(2026, 1, 1, 0, 0, 0, 1, time.UTC), func(*domain.PeriodicSchedule) {}},
		{"future delay exceeds duration range", time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC), func(s *domain.PeriodicSchedule) {
			value := time.Date(2500, 1, 1, 0, 0, 0, 0, time.UTC)
			s.LastRunAt = &value
		}},
		{"initial start cursor underflows Python datetime", time.Date(20, 1, 1, 0, 0, 0, 0, time.UTC), func(s *domain.PeriodicSchedule) {
			s.DateChanged = time.Date(20, 1, 1, 0, 0, 0, 0, time.UTC)
			value := s.DateChanged
			s.StartTime = &value
		}},
		{"next daily slot exceeds Python datetime", time.Date(9999, 12, 31, 12, 0, 0, 0, time.UTC), func(s *domain.PeriodicSchedule) {
			s.Interval = nil
			s.Cron = &domain.PeriodicCron{Minute: "0", Hour: "12", DayOfWeek: "*", DayOfMonth: "*", MonthOfYear: "*", Timezone: "UTC"}
			value := time.Date(9999, 12, 31, 12, 0, 0, 0, time.UTC)
			s.LastRunAt = &value
		}},
	}
	for _, item := range cases {
		t.Run(item.name, func(t *testing.T) {
			row := basicSchedule()
			item.prepare(&row)
			decision, err := EvaluateScheduleDue(row, item.now)
			if !errors.Is(err, domain.ErrScheduleInvalid) || decision != (domain.ScheduleDue{}) {
				t.Fatalf("%+v %v", decision, err)
			}
		})
	}
}
