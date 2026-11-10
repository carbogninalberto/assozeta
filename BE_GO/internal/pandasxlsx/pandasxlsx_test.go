package pandasxlsx

import (
	"bytes"
	"compress/gzip"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"os"
	"strconv"
	"testing"
)

type goldenCase struct {
	Text     []string     `json:"text"`
	Input    string       `json:"input"`
	Error    string       `json:"error"`
	Other    string       `json:"other"`
	Columns  []string     `json:"columns"`
	Rows     [][][]string `json:"rows"`
	Required bool         `json:"required"`
}

func rendered(value Value) []string {
	switch value.Kind {
	case NaN:
		return []string{"nan"}
	case NaT:
		return []string{"nat"}
	case String:
		return []string{"s", value.S}
	case Int:
		return []string{"i", strconv.FormatInt(value.I, 10)}
	case Float:
		return []string{"f", strconv.FormatFloat(value.F, 'g', -1, 64)}
	default:
		return []string{"t", value.TimeText()}
	}
}

func sameValue(want, got []string) bool {
	if len(want) != len(got) || want[0] != got[0] {
		return false
	}
	if want[0] == "f" {
		left, err := strconv.ParseFloat(want[1], 64)
		right, _ := strconv.ParseFloat(got[1], 64)
		return err == nil && left == right
	}
	return len(want) == 1 || want[1] == got[1]
}

// TestPandasFrames requires pandas' outcome for every workbook the package
// accepts, and acceptance of the workbooks marked required.
func TestPandasFrames(t *testing.T) {
	file, err := os.Open("testdata/pandas_frames.json.gz")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	stream, err := gzip.NewReader(file)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := io.ReadAll(stream)
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		Cases []goldenCase `json:"cases"`
	}
	if err = json.NewDecoder(bytes.NewReader(raw)).Decode(&golden); err != nil {
		t.Fatal(err)
	}
	if len(golden.Cases) < 1000 {
		t.Fatalf("only %d cases", len(golden.Cases))
	}
	accepted, failures := 0, 0
	for index, item := range golden.Cases {
		data, err := base64.StdEncoding.DecodeString(item.Input)
		if err != nil {
			t.Fatal(err)
		}
		frame, err := Read(data)
		fail := func(format string, args ...any) {
			if failures++; failures <= 25 {
				t.Errorf("case %d: "+format, append([]any{index}, args...)...)
			}
		}
		switch {
		case errors.Is(err, ErrUnsupported):
			if item.Required {
				fail("required workbook refused")
			}
			continue
		case errors.Is(err, ErrInvalid):
			if item.Error == "" {
				fail("invalid, pandas reads it")
			}
			continue
		case err != nil:
			fail("%v", err)
			continue
		}
		accepted++
		if item.Error != "" || item.Other != "" {
			fail("accepted, pandas: %s%s", item.Error, item.Other)
			continue
		}
		if len(frame.Columns) != len(item.Columns) || len(frame.Rows) != len(item.Rows) {
			fail("shape %v x %d, want %v x %d", frame.Columns, len(frame.Rows), item.Columns, len(item.Rows))
			continue
		}
		for column, name := range item.Columns {
			if frame.Columns[column] != name {
				fail("columns %q, want %q", frame.Columns, item.Columns)
				break
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
	if accepted < len(golden.Cases)/3 {
		t.Fatalf("only %d of %d workbooks accepted", accepted, len(golden.Cases))
	}
	t.Logf("%d of %d workbooks accepted", accepted, len(golden.Cases))
}

// TestPandasTextFrames requires pandas' outcome for the same workbooks read
// with the text converter on the columns each case names, for every workbook
// the package accepts, and acceptance of most of them.
func TestPandasTextFrames(t *testing.T) {
	file, err := os.Open("testdata/pandas_text_frames.json.gz")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	stream, err := gzip.NewReader(file)
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		Cases []goldenCase `json:"cases"`
	}
	if err = json.NewDecoder(stream).Decode(&golden); err != nil {
		t.Fatal(err)
	}
	if len(golden.Cases) < 500 {
		t.Fatalf("only %d cases", len(golden.Cases))
	}
	accepted, failures := 0, 0
	for index, item := range golden.Cases {
		data, err := base64.StdEncoding.DecodeString(item.Input)
		if err != nil {
			t.Fatal(err)
		}
		text := map[string]bool{}
		for _, column := range item.Text {
			text[column] = true
		}
		frame, err := ReadText(data, func(column string) bool { return text[column] })
		fail := func(format string, args ...any) {
			if failures++; failures <= 25 {
				t.Errorf("case %d: "+format, append([]any{index}, args...)...)
			}
		}
		switch {
		case errors.Is(err, ErrUnsupported):
			continue
		case err != nil:
			fail("%v", err)
			continue
		}
		accepted++
		if item.Error != "" || item.Other != "" {
			fail("accepted, pandas: %s%s", item.Error, item.Other)
			continue
		}
		if len(frame.Columns) != len(item.Columns) || len(frame.Rows) != len(item.Rows) {
			fail("shape %v x %d, want %v x %d", frame.Columns, len(frame.Rows), item.Columns, len(item.Rows))
			continue
		}
		for row, values := range item.Rows {
			for column, want := range values {
				if got := rendered(frame.Rows[row][column]); !sameValue(want, got) {
					fail("row %d column %d (text %v): %q, want %q", row, column, text[frame.Columns[column]], got, want)
				}
			}
		}
	}
	if accepted < len(golden.Cases)/2 {
		t.Fatalf("accepted %d of %d", accepted, len(golden.Cases))
	}
	t.Logf("accepted %d of %d", accepted, len(golden.Cases))
}
