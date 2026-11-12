package pdfexport

import (
	"encoding/json"
	"testing"
	"time"
)

func buildGoldenOther(t *testing.T, engine string, raw json.RawMessage, now time.Time) ([]byte, error) {
	t.Helper()
	switch engine {
	case "report_table":
		var spec struct {
			Headers    []string   `json:"headers"`
			Rows       [][]string `json:"rows"`
			Title      string     `json:"title"`
			Footer     string     `json:"footer_text"`
			TextBefore string     `json:"text_before"`
			TextAfter  string     `json:"text_after"`
		}
		if err := json.Unmarshal(raw, &spec); err != nil {
			t.Fatal(err)
		}
		return ReportTable{Headers: spec.Headers, Rows: spec.Rows, Title: spec.Title, Footer: spec.Footer, TextBefore: spec.TextBefore,
			TextAfter: spec.TextAfter}.Build(now)
	case "attendance":
		var spec struct {
			Courses []struct {
				Course string   `json:"course"`
				Dates  []string `json:"dates"`
				Rows   []struct {
					Name       string   `json:"name"`
					Attendance []string `json:"attendance"`
					Present    int      `json:"present_count"`
				} `json:"rows"`
			} `json:"courses"`
		}
		if err := json.Unmarshal(raw, &spec); err != nil {
			t.Fatal(err)
		}
		var input Attendance
		for _, course := range spec.Courses {
			item := AttendanceCourse{Course: course.Course, Dates: course.Dates}
			for _, row := range course.Rows {
				item.Rows = append(item.Rows, AttendanceRow{Name: row.Name, Attendance: row.Attendance, Present: row.Present})
			}
			input.Courses = append(input.Courses, item)
		}
		return input.Build(now)
	}
	t.Fatalf("engine %s", engine)
	return nil, nil
}
