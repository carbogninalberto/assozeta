package xlsxexport

import (
	"errors"
	"strings"
	"testing"
	"time"
)

// djangoText is str(exception) of a build the Django exporter fails on: the
// agent's tool result carries it ({"error": str(e)}).
func djangoText(t *testing.T, err error) string {
	t.Helper()
	if err == nil || !errors.Is(err, ErrDjango) {
		t.Fatalf("expected ErrDjango, got %v", err)
	}
	var failure *DjangoError
	if !errors.As(err, &failure) {
		t.Fatalf("ErrDjango without its text: %v", err)
	}
	return failure.Text
}

func TestDjangoExceptionTexts(t *testing.T) {
	now := time.Date(2026, 11, 10, 9, 8, 7, 0, time.UTC)
	data := [][]Value{{StrValue("x"), StrValue("y")}} // one column, two rows
	cell := func(label Value) ReportSheet {
		return ReportSheet{Title: "T", Columns: []string{"a"}, Labels: []Value{label}, Data: [][]Value{{StrValue("x"), StrValue("y")}}}
	}
	cases := []struct {
		name  string
		build func() error
		want  string
	}{
		{"title-slash", func() error {
			_, err := ReportWorkbook(ReportSheet{Title: "a/b", Columns: []string{"a"}, Data: data}, now)
			return err
		}, "Invalid character / found in sheet title"},
		{"title-bracket", func() error {
			_, err := ReportWorkbook(ReportSheet{Title: "a[b]", Columns: []string{"a"}, Data: data}, now)
			return err
		}, "Invalid character [ found in sheet title"},
		{"title-empty", func() error {
			_, err := ReportSheetsWorkbook([]ReportSheet{{Title: "", Columns: []string{"a"}, Data: data}}, now)
			return err
		}, "Title must have at least one character"},
		{"title-case", func() error {
			_, err := ReportSheetsWorkbook([]ReportSheet{
				{Title: "Tags", Columns: []string{"a"}, Data: data},
				{Title: "tags", Columns: []string{"a"}, Data: data}}, now)
			return err
		}, "'tags'"},
		{"title-final-sigma", func() error {
			// str.lower() maps a capital and a final sigma alike: openpyxl
			// renames the second sheet, pandas raises its KeyError.
			_, err := ReportSheetsWorkbook([]ReportSheet{
				{Title: "ΑΣ", Columns: []string{"a"}, Data: data},
				{Title: "Ας", Columns: []string{"a"}, Data: data}}, now)
			return err
		}, "'Ας'"},
		{"cell-illegal", func() error {
			_, err := ReportWorkbook(ReportSheet{Title: "T", Columns: []string{"a"}, Data: [][]Value{{StrValue("a\ab")}}}, now)
			return err
		}, "a\ab cannot be used in worksheets."},
		{"label-overflow", func() error {
			_, err := ReportWorkbook(cell(BigIntValue("1"+strings.Repeat("0", 400))), now)
			return err
		}, "int too large to convert to float"},
		{"attendance-no-rows", func() error {
			_, err := AttendanceWorkbook("single", []AttendanceCourse{{Course: "Nuoto", Dates: []string{"01/09/2026"}}}, now)
			return err
		}, "'Nome e Cognome'"},
		{"attendance-flat-no-rows", func() error {
			_, err := AttendanceWorkbook("flat", []AttendanceCourse{{Course: "Nuoto", Dates: []string{"01/09/2026"}}}, now)
			return err
		}, "'Corso'"},
		{"attendance-untitled", func() error {
			_, err := AttendanceWorkbook("single", []AttendanceCourse{{Untitled: true, Dates: []string{"01/09/2026"},
				Rows: []AttendanceRow{{Name: "Rossi", Present: 1}}}}, now)
			return err
		}, "'NoneType' object is not subscriptable"},
		{"attendance-title", func() error {
			_, err := AttendanceWorkbook("single", []AttendanceCourse{{Course: "Nuoto 1/2", Dates: []string{"01/09/2026"},
				Rows: []AttendanceRow{{Name: "Rossi", Present: 1}}}}, now)
			return err
		}, "Invalid character / found in sheet title"},
	}
	for _, item := range cases {
		t.Run(item.name, func(t *testing.T) {
			if got := djangoText(t, item.build()); got != item.want {
				t.Fatalf("text %q, want %q", got, item.want)
			}
		})
	}
}

// A PythonKeyError lookups a sheet title pandas asked for: its str() is the
// key's repr.
func TestPythonKeyError(t *testing.T) {
	if got := pythonKeyError("tags"); got != "'tags'" {
		t.Fatalf("key error %q", got)
	}
}
