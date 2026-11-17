package service

import (
	"archive/zip"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// The expected cells and widths below were produced by Django's own
// generate_xlsx_report_from_data (pandas 2.2.3 + XlsxWriter 3.2.2) with the
// reference interpreter: an int beyond int64 stays a Python int in the
// DataFrame (its digits are what astype(str) measures) and xlsxwriter's
// write_number then writes float(number); past the double range it raises
// OverflowError and generate_report_from_data returns None.

const reportAssociation = "a1000000-0000-4000-8000-000000000601"
const reportRegistry = "a1000000-0000-4000-8000-000000000602"
const reportEvent = "a1000000-0000-4000-8000-000000000603"

// reportStore seeds one registry with one all-day event of the association and
// one attendance day naming its event id.
type reportStore struct {
	events               string
	attendees            *string
	courseEvents         *string
	courseMultipleQuotes *string
}

func (s reportStore) OwnerAssociation(context.Context, string) (string, error) {
	return reportAssociation, nil
}

func (s reportStore) Instructors(context.Context, string) ([]domain.InstructorReportInstructor, error) {
	return []domain.InstructorReportInstructor{{ID: "a1000000-0000-4000-8000-000000000604", FirstName: "Anna", LastName: "Rossi"}}, nil
}

func (s reportStore) Registries(context.Context, string) ([]domain.InstructorReportRegistry, error) {
	course := "Corso A"
	events := s.events
	return []domain.InstructorReportRegistry{{ID: reportRegistry, Events: &events, Course: &course,
		CourseEvents: s.courseEvents, CourseMultipleQuotes: s.courseMultipleQuotes}}, nil
}

func (s reportStore) Days(context.Context, string, time.Time, time.Time) ([]domain.InstructorReportDay, error) {
	title := "Lezione"
	event := reportEvent
	return []domain.InstructorReportDay{{Title: &title, Date: time.Date(2026, 9, 15, 8, 0, 0, 0, time.UTC),
		Attendees: s.attendees, AssociatedEvent: &event}}, nil
}

func (s reportStore) Names(context.Context, []string) (map[string]string, error) {
	return map[string]string{}, nil
}

func reportIdentity() domain.ResolvedIdentity {
	owner := &domain.Account{ID: reportAssociation, Role: domain.RoleAssociation}
	return domain.ResolvedIdentity{Actor: owner, Effective: owner, ScopeUser: owner}
}

// reportEventJSON is one all-day event whose extendedProps.description is the
// given JSON text and whose instructor names the store's instructor.
func reportEventJSON(description string) string {
	return `[{"event_id": "` + reportEvent + `", "title": "Lezione", "extendedProps": {"instructor": {"instructor_id": ` +
		`"a1000000-0000-4000-8000-000000000604"}, "description": ` + description + `}}]`
}

func reportRun(t *testing.T, store domain.InstructorReportStore) domain.RequestObject {
	t.Helper()
	now := time.Date(2026, 9, 30, 12, 0, 0, 0, time.UTC)
	start, end := "15/09/2026", "15/09/2026"
	response, err := NewInstructorReport(store, func() time.Time { return now }).Report(context.Background(), reportIdentity(), &start, &end)
	if err != nil {
		t.Fatal(err)
	}
	return response.(domain.RequestObject)
}

func reportField(t *testing.T, response domain.RequestObject, name string) any {
	t.Helper()
	data, _ := response.Lookup("data")
	value, ok := data.(domain.RequestObject).Lookup(name)
	if !ok {
		t.Fatalf("no %s", name)
	}
	return value
}

func reportSheet(t *testing.T, encoded any) string {
	t.Helper()
	text, ok := encoded.(string)
	if !ok {
		t.Fatalf("report_file %#v", encoded)
	}
	workbook, err := base64.StdEncoding.DecodeString(text)
	if err != nil {
		t.Fatal(err)
	}
	archive, err := zip.NewReader(bytes.NewReader(workbook), int64(len(workbook)))
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range archive.File {
		if entry.Name == "xl/worksheets/sheet1.xml" {
			file, err := entry.Open()
			if err != nil {
				t.Fatal(err)
			}
			sheet, err := io.ReadAll(file)
			if err != nil {
				t.Fatal(err)
			}
			return string(sheet)
		}
	}
	t.Fatal("no sheet")
	return ""
}

// An int beyond int64 but inside the double range is written as its float
// (Django: <c r="E2"><v>9.223372036854776E+18</v></c>) while the column width
// still measures its 19 digits (Django: 21.7109375).
func TestInstructorReportBigIntDescription(t *testing.T) {
	response := reportRun(t, reportStore{events: reportEventJSON("9223372036854775808")})
	results := reportField(t, response, "results").([]any)
	row := results[0].(domain.RequestObject)
	if description, _ := row.Lookup("description"); description != json.Number("9223372036854775808") {
		t.Fatalf("description %#v", description)
	}
	sheet := reportSheet(t, reportField(t, response, "report_file"))
	if !strings.Contains(sheet, `<c r="E2"><v>9.223372036854776E+18</v></c>`) {
		t.Fatalf("cell: %s", sheet)
	}
	if !strings.Contains(sheet, `<col min="5" max="5" width="21.7109375" customWidth="1"/>`) {
		t.Fatalf("width: %s", sheet)
	}
}

// 2**64-1, -2**63-1 and 2**64 keep their own digits for the width and the
// float xlsxwriter writes.
func TestInstructorReportBigIntBoundaries(t *testing.T) {
	for text, cell := range map[string]string{
		"18446744073709551615": `<v>1.844674407370955E+19</v>`,
		"-9223372036854775809": `<v>-9.223372036854776E+18</v>`,
		"18446744073709551616": `<v>1.844674407370955E+19</v>`,
		"9223372036854775807":  `<v>9.223372036854776E+18</v>`, // in int64: an IntValue already
		"-9223372036854775808": `<v>-9.223372036854776E+18</v>`,
		"12345678901234567890": `<v>1.234567890123457E+19</v>`,
		"99999999999999999999": `<v>1E+20</v>`,
	} {
		sheet := reportSheet(t, reportField(t, reportRun(t, reportStore{events: reportEventJSON(text)}), "report_file"))
		if !strings.Contains(sheet, `r="E2">`+cell) {
			t.Fatalf("%s: %s", text, sheet)
		}
	}
}

// Past the double range xlsxwriter raises OverflowError, which
// generate_report_from_data logs: the response keeps the row and report_file
// null.
func TestInstructorReportDescriptionPastDoubleIsNullReport(t *testing.T) {
	for _, text := range []string{"1" + strings.Repeat("0", 400), "-1" + strings.Repeat("0", 400)} {
		response := reportRun(t, reportStore{events: reportEventJSON(text)})
		if report := reportField(t, response, "report_file"); report != nil {
			t.Fatalf("%s: report_file %#v", text, report)
		}
		results := reportField(t, response, "results").([]any)
		if row := results[0].(domain.RequestObject); len(row) != 9 {
			t.Fatalf("row %#v", row)
		}
	}
}

// twoDayStore is one registry whose two days carry a description beyond int64
// and a float: pandas makes the column float64, so the width comes from the
// float reprs of both values, not from the int digits (Django: 23.7109375).
type twoDayStore struct{ reportStore }

func (s twoDayStore) Days(context.Context, string, time.Time, time.Time) ([]domain.InstructorReportDay, error) {
	first, second := "Prima", "Seconda"
	one, two := reportEvent, "a1000000-0000-4000-8000-000000000605"
	return []domain.InstructorReportDay{
		{Title: &first, Date: time.Date(2026, 9, 15, 8, 0, 0, 0, time.UTC), AssociatedEvent: &one},
		{Title: &second, Date: time.Date(2026, 9, 16, 8, 0, 0, 0, time.UTC), AssociatedEvent: &two},
	}, nil
}

func TestInstructorReportIntAndFloatColumn(t *testing.T) {
	events := `[{"event_id": "` + reportEvent + `", "title": "Prima", "extendedProps": {"instructor": {"instructor_id": ` +
		`"a1000000-0000-4000-8000-000000000604"}, "description": 9223372036854775808}}, ` +
		`{"event_id": "a1000000-0000-4000-8000-000000000605", "title": "Seconda", "extendedProps": {"instructor": ` +
		`{"instructor_id": "a1000000-0000-4000-8000-000000000604"}, "description": 1.5}}]`
	sheet := reportSheet(t, reportField(t, reportRun(t, twoDayStore{reportStore{events: events}}), "report_file"))
	if !strings.Contains(sheet, `<c r="E2"><v>9.223372036854776E+18</v></c>`) || !strings.Contains(sheet, `<c r="E3"><v>1.5</v></c>`) {
		t.Fatalf("cells: %s", sheet)
	}
	if !strings.Contains(sheet, `<col min="5" max="5" width="23.7109375" customWidth="1"/>`) {
		t.Fatalf("width: %s", sheet)
	}
}

// Registry events and attendance lists nested 600 and 2000 levels are what
// jsonb stores and json.loads reads: the view answers the report.
func TestInstructorReportDeepStoredJSON(t *testing.T) {
	deep := func(depth int) string { return strings.Repeat(`[`, depth) + strings.Repeat(`]`, depth) }
	for _, depth := range []int{600, 2000} {
		events := `[{"event_id": "` + reportEvent + `", "title": "Lezione", "extendedProps": {"instructor": {"instructor_id": ` +
			`"a1000000-0000-4000-8000-000000000604"}, "extra": ` + deep(depth) + `}}]`
		sheet := reportSheet(t, reportField(t, reportRun(t, reportStore{events: events}), "report_file"))
		if !strings.Contains(sheet, `<c r="A2" t="s">`) {
			t.Fatalf("depth %d: %s", depth, sheet)
		}
		attendees := `{"course_subscription_id": null, "extra": ` + deep(depth) + `}`
		list := `[` + attendees + `]`
		response := reportRun(t, reportStore{events: reportEventJSON(`""`), attendees: &list})
		if report := reportField(t, response, "report_file"); report == nil {
			t.Fatalf("attendees %d: no report", depth)
		}
	}
}

// json.loads' RecursionError is the 500 page past the measured edge (owner
// decision X15); at the edge the report is answered. The Course JSON columns
// select_related loads share the same edge.
func TestInstructorReportStoredJSONNesting(t *testing.T) {
	deep := func(depth int) string { return strings.Repeat(`[`, depth) + strings.Repeat(`]`, depth) }
	// The event's own container nesting is 3 above the extra list, and the
	// attendee list's 2.
	event := func(total int) string {
		return `[{"event_id": "` + reportEvent + `", "title": "Lezione", "extendedProps": {"instructor": ` +
			`{"instructor_id": "a1000000-0000-4000-8000-000000000604"}, "extra": ` + deep(total-3) + `}}]`
	}
	attendees := func(total int) string {
		return `[{"course_subscription_id": null, "extra": ` + deep(total-2) + `}]`
	}
	for _, total := range []int{instructorReportJSONEdge - 4, instructorReportJSONEdge - 1, instructorReportJSONEdge} {
		response := reportRun(t, reportStore{events: event(total)})
		if reportField(t, response, "report_file") == nil {
			t.Fatalf("events depth %d: no report", total)
		}
		response = reportRun(t, reportStore{events: reportEventJSON(`""`), attendees: reportText(attendees(total))})
		if reportField(t, response, "report_file") == nil {
			t.Fatalf("attendees depth %d: no report", total)
		}
		for _, column := range []string{"events", "multiple_quotes"} {
			registry := reportStore{events: reportEventJSON(`""`)}
			if column == "events" {
				registry.courseEvents = reportText(deep(total))
			} else {
				registry.courseMultipleQuotes = reportText(deep(total))
			}
			if report := reportField(t, reportRun(t, registry), "report_file"); report == nil {
				t.Fatalf("course %s depth %d: no report", column, total)
			}
		}
	}
	for _, total := range []int{instructorReportJSONEdge + 1, instructorReportJSONEdge + 30} {
		for name, store := range map[string]reportStore{
			"events":        {events: event(total)},
			"attendees":     {events: reportEventJSON(`""`), attendees: reportText(attendees(total))},
			"course-events": {events: reportEventJSON(`""`), courseEvents: reportText(deep(total))},
			"course-quotes": {events: reportEventJSON(`""`), courseMultipleQuotes: reportText(deep(total))},
		} {
			_, err := NewInstructorReport(store, time.Now).Report(context.Background(), reportIdentity(),
				reportPtr("15/09/2026"), reportPtr("15/09/2026"))
			if !errors.Is(err, ErrInstructorReportServer) || errors.Is(err, refusal.ErrNotPorted) {
				t.Fatalf("%s depth %d: %v", name, total, err)
			}
		}
	}
}

// The description's DataFrame value types: str, bool, float, list and dict are
// written by xlsxwriter as text, a bool cell, a number and the reprs.
func TestInstructorReportDescriptionTypes(t *testing.T) {
	for description, cell := range map[string]string{
		`true`:       `<c r="E2" t="b"><v>1</v></c>`,
		`1.5`:        `<c r="E2"><v>1.5</v></c>`,
		`1e400`:      `<c r="E2" t="s">`,
		`["a", 1]`:   `<c r="E2" t="s">`,
		`{"k": "v"}`: `<c r="E2" t="s">`,
	} {
		sheet := reportSheet(t, reportField(t, reportRun(t, reportStore{events: reportEventJSON(description)}), "report_file"))
		if !strings.Contains(sheet, cell) {
			t.Fatalf("%s: %s", description, sheet)
		}
	}
}

func reportPtr(value string) *string  { return &value }
func reportText(value string) *string { return &value }
