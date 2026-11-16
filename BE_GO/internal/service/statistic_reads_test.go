package service

import (
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// The expiredPayments band (owner decision X15): psycopg2 loads Payment.course
// and Payment.meta with json.loads, so 9936 levels load and 9937 are the view's
// unhandled exception (the 500 page). The band is the harness's measured one,
// not a number this test derives.
func TestStatisticExpiredPaymentJSONDepthBand(t *testing.T) {
	const measured = 9936
	if statisticExpiredPaymentJSONDepth != measured {
		t.Fatalf("band: %d, want %d", statisticExpiredPaymentJSONDepth, measured)
	}
	value := func(levels int) string {
		return `{"label": "Corso", "unused": ` + strings.Repeat("[", levels) + strings.Repeat("]", levels) + `}`
	}
	atBand := value(measured - 1) // the object is the last level
	overBand := value(measured)
	if dto.AuditJSONDepth(atBand) != measured || dto.AuditJSONDepth(overBand) != measured+1 {
		t.Fatalf("authored depths: %d %d", dto.AuditJSONDepth(atBand), dto.AuditJSONDepth(overBand))
	}
	loaded, err := statisticStoredJSON(&atBand)
	if err != nil {
		t.Fatalf("at the band: %v", err)
	}
	object, ok := loaded.(domain.RequestObject)
	if !ok || len(object) != 2 {
		t.Fatalf("at the band: %#v", loaded)
	}
	if label, found := object.Lookup("label"); !found || label != "Corso" {
		t.Fatalf("at the band: label %#v %v", label, found)
	}
	if _, err := statisticStoredJSON(&overBand); !errors.Is(err, ErrStatisticPythonError) {
		t.Fatalf("past the band: %v", err)
	}
	if loaded, err := statisticStoredJSON(nil); loaded != nil || err != nil {
		t.Fatalf("absent column: %#v %v", loaded, err)
	}
}

func TestBalanceSheetRange(t *testing.T) {
	at := func(text string) time.Time {
		value, err := time.Parse(time.RFC3339, text)
		if err != nil {
			t.Fatal(err)
		}
		return value
	}
	cases := []struct {
		moment     string
		day, month int
		from, to   string
	}{
		{"2025-03-10T00:00:00Z", 1, 9, "2024-09-01", "2025-08-31"},
		{"2024-09-01T00:00:00Z", 1, 9, "2024-09-01", "2025-08-31"},
		{"2024-08-31T23:59:59Z", 1, 9, "2023-09-01", "2024-08-31"},
		{"2026-11-01T21:00:00Z", 1, 1, "2026-01-01", "2026-12-31"},
		{"2026-03-01T00:00:00Z", 15, 3, "2025-03-15", "2026-03-14"},
	}
	for _, c := range cases {
		year, err := BalanceSheetRange(at(c.moment), c.day, c.month)
		if err != nil || year.From.Format("2006-01-02") != c.from || year.To.Format("2006-01-02") != c.to ||
			year.To.Hour() != 0 || year.From.Location() != time.UTC {
			t.Errorf("%v: got %v %v", c, year, err)
		}
	}
	// datetime() raises for impossible combinations, including the previous year's 29 February.
	for _, c := range []struct {
		moment     string
		day, month int
	}{
		{"2025-03-10T00:00:00Z", 30, 2}, {"2024-03-10T00:00:00Z", 29, 2}, {"2025-03-10T00:00:00Z", 1, 13}, {"2025-03-10T00:00:00Z", 0, 1},
	} {
		if _, err := BalanceSheetRange(at(c.moment), c.day, c.month); err == nil {
			t.Errorf("%v: expected ValueError", c)
		}
	}
}

func TestStatisticMonths(t *testing.T) {
	names, numbers := statisticMonths(9)
	if names[0] != "Set" || names[11] != "Ago" || numbers[0] != 9 || numbers[4] != 1 || numbers[11] != 8 {
		t.Errorf("%v %v", names, numbers)
	}
}

// statisticRecentLesson is any(datetime.strptime(lesson['date'], '%Y-%m-%d %H:%M:%S%z') > since):
// a registry date of another script is read where CPython's \d patterns accept it and is the caught
// ValueError otherwise, never a refusal of the whole widget.
func TestStatisticRecentLessonReadsStoredRegistryDates(t *testing.T) {
	since := time.Date(2026, 10, 17, 10, 0, 0, 0, time.UTC)
	meta := func(date string) domain.RequestObject {
		value, err := dto.ParseReferenceJSON([]byte(`{"lessons_left": 0, "lessons_registry": [{"date": "` + date + `"}]}`))
		if err != nil {
			t.Fatal(err)
		}
		object, ok := value.(domain.RequestObject)
		if !ok {
			t.Fatalf("meta: %T", value)
		}
		return object
	}
	cases := map[string]bool{
		"٢٠26-11-11 1٠:0٠:0٠+0000": true,  // 5 days before the 2026-11-16 anchor: after the 30-day bound
		"٢٠26-10-07 1٠:0٠:0٠+0000": false, // 40 days before the anchor: before the bound
		"2026-11-11 10:00:00+0000": true,
		"٢٠٢٦-١١-١٠ ١٠:٠٠:٠٠+0000": false, // %m is ASCII only: ValueError, the carnet is skipped
		"garbage":                  false,
	}
	for date, want := range cases {
		got, err := statisticRecentLesson(meta(date), since)
		if err != nil {
			t.Errorf("%q: %v", date, err)
			continue
		}
		if got != want {
			t.Errorf("%q: got %v, want %v", date, got, want)
		}
	}
}
