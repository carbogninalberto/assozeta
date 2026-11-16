package xlsxexport

import (
	"bytes"
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"os"
	"runtime"
	"strconv"
	"strings"
	"testing"
	"time"
)

// The golden workbooks come from Django's own functions
// (TESTS/fixtures/generate_xlsx_golden.py); a case Django raises on must
// fail here with ErrDjango.

type goldenEntry struct {
	Name     string `json:"name"`
	Method   uint16 `json:"method"`
	Flags    uint16 `json:"flags"`
	External uint32 `json:"external"`
	SHA256   string `json:"sha256"`
}

type goldenCase struct {
	Name    string          `json:"name"`
	Kind    string          `json:"kind"`
	Input   json.RawMessage `json:"input"`
	Raises  string          `json:"raises"`
	Entries []goldenEntry   `json:"entries"`
}

type golden struct {
	Cases []goldenCase      `json:"cases"`
	Parts map[string]string `json:"parts"`
}

// goldenPlatform are the golden inputs whose workbook depends on the platform
// the pandas wheel was built for (preciseXstrtod): the golden file was
// captured on macOS arm64, so they are compared there only.
var goldenPlatform = map[string]string{
	"eu-long-digits": "darwin/arm64", // 17+ significant digits: the fused digit accumulation
}

func decodeValue(t *testing.T, raw json.RawMessage) Value {
	t.Helper()
	if string(raw) == "null" {
		return NoneValue()
	}
	var text string
	if err := json.Unmarshal(raw, &text); err == nil {
		return StrValue(text)
	}
	var tagged map[string]json.RawMessage
	if err := json.Unmarshal(raw, &tagged); err != nil || len(tagged) != 1 {
		t.Fatalf("bad value %s", raw)
	}
	for kind, body := range tagged {
		var scalar string
		switch kind {
		case "int":
			json.Unmarshal(body, &scalar)
			number, err := strconv.ParseInt(scalar, 10, 64)
			if err != nil {
				if _, ok := new(big.Int).SetString(scalar, 10); !ok {
					t.Fatalf("int %s", scalar)
				}
				return BigIntValue(scalar)
			}
			return IntValue(number)
		case "float":
			json.Unmarshal(body, &scalar)
			switch scalar {
			case "inf":
				return FloatValue(math.Inf(1))
			case "-inf":
				return FloatValue(math.Inf(-1))
			case "nan":
				return FloatValue(math.NaN())
			}
			number, err := strconv.ParseFloat(scalar, 64)
			if err != nil {
				t.Fatalf("float %s", scalar)
			}
			return FloatValue(number)
		case "decimal":
			json.Unmarshal(body, &scalar)
			return DecimalValue(scalar)
		case "bool":
			var value bool
			json.Unmarshal(body, &value)
			return BoolValue(value)
		case "list", "dict":
			return ObjectValue(goldenRepr(t, raw))
		}
	}
	t.Fatalf("bad value %s", raw)
	return Value{}
}

// goldenRepr is the Python repr of a tagged list/dict (str and int items only).
func goldenRepr(t *testing.T, raw json.RawMessage) string {
	t.Helper()
	var text string
	if string(raw) == "null" {
		return "None"
	}
	if err := json.Unmarshal(raw, &text); err == nil {
		return "'" + text + "'"
	}
	var tagged map[string]json.RawMessage
	json.Unmarshal(raw, &tagged)
	if body, ok := tagged["int"]; ok {
		json.Unmarshal(body, &text)
		return text
	}
	if body, ok := tagged["list"]; ok {
		var items []json.RawMessage
		json.Unmarshal(body, &items)
		out := "["
		for i, item := range items {
			if i > 0 {
				out += ", "
			}
			out += goldenRepr(t, item)
		}
		return out + "]"
	}
	var pairs [][2]json.RawMessage
	json.Unmarshal(tagged["dict"], &pairs)
	out := "{"
	for i, pair := range pairs {
		if i > 0 {
			out += ", "
		}
		out += goldenRepr(t, pair[0]) + ": " + goldenRepr(t, pair[1])
	}
	return out + "}"
}

func decodeRow(t *testing.T, raw []json.RawMessage) []Value {
	row := make([]Value, len(raw))
	for i, item := range raw {
		row[i] = decodeValue(t, item)
	}
	return row
}

func buildGolden(t *testing.T, item goldenCase, now time.Time) ([]byte, error) {
	switch item.Kind {
	case "excel_utils":
		var input struct {
			Columns []string            `json:"columns"`
			Data    [][]json.RawMessage `json:"data"`
		}
		json.Unmarshal(item.Input, &input)
		data := make([][]Value, len(input.Data))
		for i, column := range input.Data {
			data[i] = decodeRow(t, column)
		}
		return PandasOpenpyxl(data, input.Columns, now)
	case "instructor":
		var input struct {
			Schema [][2]string         `json:"schema"`
			Rows   [][]json.RawMessage `json:"rows"`
		}
		json.Unmarshal(item.Input, &input)
		frame := Frame{Sheet: "Sheet1"}
		if len(input.Rows) > 0 {
			for _, pair := range input.Schema {
				frame.Columns = append(frame.Columns, pair[1])
			}
		}
		for _, row := range input.Rows {
			frame.Rows = append(frame.Rows, decodeRow(t, row))
		}
		for c, name := range frame.Columns {
			column := make([]Value, len(frame.Rows))
			for r, row := range frame.Rows {
				column[r] = row[c]
			}
			longest, err := StrLength(column)
			if err != nil {
				return nil, err
			}
			frame.Widths = append(frame.Widths, float64(max(longest, pyLen(name))+2))
		}
		return PandasXlsxwriter(frame, now)
	case "subscription", "module":
		var input struct {
			Columns []string            `json:"columns"`
			Rows    [][]json.RawMessage `json:"rows"`
		}
		json.Unmarshal(item.Input, &input)
		if len(input.Rows) == 0 {
			return nil, ErrDjango // DataFrame([]).columns = cols: length mismatch
		}
		frame := Frame{Sheet: "Libro Soci", Columns: input.Columns, Index: true}
		if item.Kind == "module" {
			frame.Sheet, frame.Index = "Risposte", false
		}
		for _, row := range input.Rows {
			frame.Rows = append(frame.Rows, decodeRow(t, row))
		}
		for _, name := range frame.Columns {
			frame.Widths = append(frame.Widths, float64(pyLen(name)+10))
		}
		return PandasXlsxwriter(frame, now)
	case "report_xlsx", "report_sheets":
		unhashable := false
		type sheetInput struct {
			Title   string              `json:"title"`
			Columns []json.RawMessage   `json:"columns"`
			Rows    [][]json.RawMessage `json:"rows"`
		}
		decode := func(input sheetInput) ReportSheet {
			sheet := ReportSheet{Title: input.Title, Data: make([][]Value, len(input.Columns))}
			for _, raw := range input.Columns {
				label := decodeValue(t, raw)
				if label.Kind == Object {
					unhashable = true // a list or dict label: the caller raises before the workbook
				}
				sheet.Labels = append(sheet.Labels, label)
			}
			sheet.Columns = LabelTexts(sheet.Labels)
			for _, row := range input.Rows {
				for c, value := range decodeRow(t, row) {
					sheet.Data[c] = append(sheet.Data[c], value)
				}
			}
			return sheet
		}
		if item.Kind == "report_xlsx" {
			var input sheetInput
			json.Unmarshal(item.Input, &input)
			sheet := decode(input)
			if unhashable {
				return nil, ErrDjango
			}
			return ReportWorkbook(sheet, now)
		}
		var input struct {
			Sheets []sheetInput `json:"sheets"`
		}
		json.Unmarshal(item.Input, &input)
		var sheets []ReportSheet
		for _, sheet := range input.Sheets {
			sheets = append(sheets, decode(sheet))
		}
		return ReportSheetsWorkbook(sheets, now)
	case "attendance":
		var input struct {
			Mode    string `json:"mode"`
			Courses []struct {
				Course *string  `json:"course"`
				Dates  []string `json:"dates"`
				Rows   []struct {
					Name       string   `json:"name"`
					Attendance []string `json:"attendance"`
					Present    int64    `json:"present_count"`
				} `json:"rows"`
			} `json:"courses"`
		}
		json.Unmarshal(item.Input, &input)
		var courses []AttendanceCourse
		for _, course := range input.Courses {
			entry := AttendanceCourse{Untitled: course.Course == nil, Dates: course.Dates}
			if course.Course != nil {
				entry.Course = *course.Course
			}
			for _, row := range course.Rows {
				entry.Rows = append(entry.Rows, AttendanceRow{Name: row.Name, Attendance: row.Attendance, Present: row.Present})
			}
			courses = append(courses, entry)
		}
		return AttendanceWorkbook(input.Mode, courses, now)
	case "all_data":
		var input struct {
			Sheets [][2]json.RawMessage `json:"sheets"`
		}
		json.Unmarshal(item.Input, &input)
		var titles []string
		var sheets [][][]string
		for _, sheet := range input.Sheets {
			var title string
			var rows [][]string
			json.Unmarshal(sheet[0], &title)
			json.Unmarshal(sheet[1], &rows)
			titles, sheets = append(titles, title), append(sheets, rows)
		}
		return OpenpyxlRows(titles, sheets, now)
	}
	t.Fatalf("unknown kind %s", item.Kind)
	return nil, nil
}

// A Python int beyond int64 in a frame is float(number) written with
// '%.16G' (xlsxwriter's write_number); beyond the double range float()
// raises OverflowError, Django's 500 page.
func TestPandasXlsxwriterBigInt(t *testing.T) {
	now := time.Date(2026, time.November, 16, 9, 0, 0, 0, time.UTC)
	build := func(digits string) ([]byte, error) {
		return PandasXlsxwriter(Frame{Sheet: "Risposte", Columns: []string{"Grande"},
			Rows: [][]Value{{BigIntValue(digits)}}}, now)
	}
	workbook, err := build("123456789012345678901234567890")
	if err != nil {
		t.Fatal(err)
	}
	parts, err := Parts(workbook)
	if err != nil {
		t.Fatal(err)
	}
	sheet := ""
	for _, part := range parts {
		if part.Name == "xl/worksheets/sheet1.xml" {
			sheet = string(part.Data)
		}
	}
	if !strings.Contains(sheet, "<v>1.234567890123457E+29</v>") {
		t.Fatalf("sheet has no float cell: %s", sheet)
	}
	if _, err := build("1" + strings.Repeat("0", 400)); !errors.Is(err, ErrDjango) {
		t.Fatalf("got %v, want Django's OverflowError", err)
	}
}

func TestGoldenWorkbooks(t *testing.T) {
	raw, err := os.ReadFile("testdata/golden.json")
	if err != nil {
		t.Fatal(err)
	}
	var source golden
	if err := json.Unmarshal(raw, &source); err != nil {
		t.Fatal(err)
	}
	if len(source.Cases) < 60 {
		t.Fatalf("only %d golden cases", len(source.Cases))
	}
	for _, item := range source.Cases {
		t.Run(item.Name, func(t *testing.T) {
			from := time.Now()
			workbook, err := buildGolden(t, item, from)
			to := time.Now()
			if platform, ok := goldenPlatform[item.Name]; ok && platform != runtime.GOOS+"/"+runtime.GOARCH {
				t.Skipf("golden output of %s", platform)
			}
			if item.Raises != "" {
				if !errors.Is(err, ErrDjango) {
					t.Fatalf("Django raises %s; Go returned %v", item.Raises, err)
				}
				return
			}
			if err != nil {
				t.Fatalf("build: %v", err)
			}
			parts, err := Parts(workbook)
			if err != nil {
				t.Fatal(err)
			}
			if len(parts) != len(item.Entries) {
				names := []string{}
				for _, part := range parts {
					names = append(names, part.Name)
				}
				t.Fatalf("entries: expected %d, got %d %v", len(item.Entries), len(parts), names)
			}
			for i, want := range item.Entries {
				got := parts[i]
				if want.Name != got.Name || want.Method != got.Method || want.Flags != got.Flags || want.External != got.External {
					t.Fatalf("entry %d: expected %+v, got %s %d %d %#x", i, want, got.Name, got.Method, got.Flags, got.External)
				}
				expected := []byte(source.Parts[want.SHA256])
				actual := got.Data
				if want.Name == "docProps/core.xml" {
					expected, _, _ = CoreTimes(expected)
					var stamps []time.Time
					if actual, stamps, err = CoreTimes(actual); err != nil {
						t.Fatal(err)
					}
					for _, stamp := range stamps {
						if stamp.Before(from.UTC().Truncate(time.Second)) || stamp.After(to.UTC()) {
							t.Fatalf("core time %s outside the build", stamp)
						}
					}
				}
				if !bytes.Equal(expected, actual) {
					t.Fatalf("part %s: %s", want.Name, firstDifference(expected, actual))
				}
			}
			// The standard's own comparator accepts the workbook against itself.
			if err := Equivalent(workbook, workbook, from, to); err != nil {
				t.Fatal(err)
			}
		})
	}
}
