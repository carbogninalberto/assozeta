package pandasxlsx

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"math"
	"os"
	"strings"
	"testing"
)

// The two files below are read by pandas itself, from workbooks assembled part
// by part in the probe scripts of the port: raise_cases.json holds the
// workbooks read_excel raises on (with the exception class) and
// ported_cases.json the ones it answers, with the frame (or the refusal of one
// whose values pandas keeps as Python objects the frame model does not carry).

type raiseCase struct {
	Name     string `json:"name"`
	Workbook string `json:"workbook"`
	Error    string `json:"error"`
}

type portedCase struct {
	Name     string       `json:"name"`
	Workbook string       `json:"workbook"`
	Labels   [][]string   `json:"labels"`
	Rows     [][][]string `json:"rows"`
}

func workbookCases(t *testing.T, name string, target any) {
	t.Helper()
	raw, err := os.ReadFile(name)
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(raw, target); err != nil {
		t.Fatal(err)
	}
}

// TestRaiseCases requires ErrInvalid for every workbook pandas raises on.
func TestRaiseCases(t *testing.T) {
	var corpus struct {
		Cases []raiseCase `json:"cases"`
	}
	workbookCases(t, "testdata/raise_cases.json", &corpus)
	if len(corpus.Cases) < 50 {
		t.Fatalf("only %d cases", len(corpus.Cases))
	}
	for index, item := range corpus.Cases {
		data, err := base64.StdEncoding.DecodeString(item.Workbook)
		if err != nil {
			t.Fatalf("case %d (%s): %v", index, item.Name, err)
		}
		if item.Error == "" {
			t.Fatalf("case %d (%s): no exception class", index, item.Name)
		}
		frame, err := ReadAll(data, nil)
		if !errors.Is(err, ErrInvalid) {
			t.Errorf("case %d (%s): %v, %v; pandas raises %s", index, item.Name, frame, err, item.Error)
		}
	}
}

// TestPortedCases requires the frame pandas answers for every workbook the
// package reads.
func TestPortedCases(t *testing.T) {
	var corpus struct {
		Cases []portedCase `json:"cases"`
	}
	workbookCases(t, "testdata/ported_cases.json", &corpus)
	if len(corpus.Cases) < 45 {
		t.Fatalf("only %d cases", len(corpus.Cases))
	}
	frames := 0
	for index, item := range corpus.Cases {
		data, err := base64.StdEncoding.DecodeString(item.Workbook)
		if err != nil {
			t.Fatalf("case %d (%s): %v", index, item.Name, err)
		}
		frame, err := ReadAll(data, nil)
		if err != nil {
			t.Errorf("case %d (%s): %v", index, item.Name, err)
			continue
		}
		frames++
		fail := func(format string, args ...any) {
			t.Errorf("case %d (%s): "+format, append([]any{index, item.Name}, args...)...)
		}
		if len(frame.Labels) != len(item.Labels) || len(frame.Rows) != len(item.Rows) {
			fail("shape %v x %d, want %d labels x %d rows", frame.Columns, len(frame.Rows), len(item.Labels),
				len(item.Rows))
			continue
		}
		for column, want := range item.Labels {
			if got := rendered(frame.Labels[column]); !sameValue(want, got) || frame.Columns[column] != want[1] {
				fail("label %d: %q (%q), want %q", column, got, frame.Columns[column], want)
			}
		}
		for row, values := range item.Rows {
			for column, want := range values {
				if got := rendered(frame.Rows[row][column]); !sameValue(want, got) {
					fail("row %d column %d: %q, want %q", row, column, got, want)
				}
			}
		}
	}
	t.Logf("%d frames", frames)
}

// TestCellColumn is openpyxl's coordinate_to_tuple and
// column_index_from_string over the references a workbook may write.
func TestCellColumn(t *testing.T) {
	columns := map[string]int64{"A1": 1, "a1": 1, "Z9": 26, "AA1": 27, "ZZZ1": 18278, "XFE1": 16385, "A0": 1,
		"A01": 1, "A1_0": 1}
	for reference, want := range columns {
		got, err := cellColumn(reference)
		if err != nil || got != want {
			t.Errorf("%q: %d, %v; want %d", reference, got, err, want)
		}
	}
	// Python's str.upper() of ß is SS, and column_index_from_string reads the
	// last three letters of it.
	expansions := map[string]int64{"\u00df1": 513, "\u00df\u00df1": 13357, "\u00df\u00df\u00df1": 13357,
		"\ufb001": 162}
	for reference, want := range expansions {
		got, err := cellColumn(reference)
		if err != nil || got != want {
			t.Errorf("%q: %d, %v; want %d", reference, got, err, want)
		}
	}
	for _, reference := range []string{"A", "1A", "A1B2", "A1.5", "ABCD1", "A0.5", "", "A 1", "A+1", "A-1", "A_1",
		"A\u06621", "\u00e01", "\u01301"} {
		if _, err := cellColumn(reference); !errors.Is(err, ErrInvalid) {
			t.Errorf("%q: %v, want ErrInvalid", reference, err)
		}
	}
}

// TestPythonNumbers is int(text) and float(text) as the cell and row parsers
// read them, and the row number openpyxl takes from int() or float().
func TestPythonNumbers(t *testing.T) {
	integers := map[string]string{"1": "1", " 1 ": "1", "+1": "1", "-1": "-1", "1_0": "10", "\u0663": "3",
		"\u0661\u0662": "12", "000": "0", "-0": "0"}
	for text, want := range integers {
		got, ok := pythonInt(text)
		if !ok || got.String() != want {
			t.Errorf("int(%q) = %v, %v; want %s", text, got, ok, want)
		}
	}
	for _, text := range []string{"", " ", "1.0", "1e3", "_1", "1_", "1__0", "+_1", "0x10", "1,5", "inf", "nan",
		"1 2"} {
		if got, ok := pythonInt(text); ok {
			t.Errorf("int(%q) = %v, want a refusal", text, got)
		}
	}
	if got, ok := pythonInt("\u0663\u0663\u0663"); !ok || got.String() != "333" {
		t.Errorf("int of three Arabic-Indic digits = %v, %v", got, ok)
	}
	if got, ok := pythonInt("1_000_000"); !ok || got.String() != "1000000" {
		t.Errorf("int(1_000_000) = %v, %v", got, ok)
	}
	if _, ok := pythonInt(strings.Repeat("1", 4301)); ok {
		t.Error("int of 4301 digits accepted")
	}
	if got, ok := pythonInt(strings.Repeat("1", 4300)); !ok || got.BitLen() < 14280 {
		t.Errorf("int of 4300 digits = %v digits, %v", got.BitLen(), ok)
	}
	floats := map[string]float64{"1.5": 1.5, " 1.5 ": 1.5, "+1.5": 1.5, "-1.5": -1.5, "1_0.5": 10.5, "1.": 1,
		".5": 0.5, "1e3": 1000, "1E-2": 0.01, "\u0661\u0662.\u0665": 12.5, "0012.50": 12.5}
	for text, want := range floats {
		got, ok := pythonFloat(text)
		if !ok || got != want {
			t.Errorf("float(%q) = %v, %v; want %v", text, got, ok, want)
		}
	}
	for _, text := range []string{"", ".", "1.2.3", "1e", "1e+", "1__0", "0x10", "1,5", "1e5e5", "inf5"} {
		if got, ok := pythonFloat(text); ok {
			t.Errorf("float(%q) = %v, want a refusal", text, got)
		}
	}
	rows := map[string]int64{"1": 1, " 3 ": 3, "+2": 2, "1_0": 10, "5.0": 5, "1e3": 1000, "\u0662": 2, "0": 0, "-5": -5,
		"1048577": 1048577, "1e9": 1000000000}
	for text, want := range rows {
		got, err := rowNumber(text)
		if err != nil || got != want {
			t.Errorf("row %q: %d, %v; want %d", text, got, err, want)
		}
	}
	for _, text := range []string{"x", "1.5", "nan", "NaN", ""} {
		if _, err := rowNumber(text); !errors.Is(err, ErrInvalid) {
			t.Errorf("row %q: %v, want ErrInvalid", text, err)
		}
	}
	// A number past int64 is past the frame bound either way: it saturates.
	for text, want := range map[string]int64{"99999999999999999999": math.MaxInt64, "-99999999999999999999": math.MinInt64,
		"1E+30": math.MaxInt64} {
		if got, err := rowNumber(text); err != nil || got != want {
			t.Errorf("row %q: %d, %v; want %d", text, got, err, want)
		}
	}
}
