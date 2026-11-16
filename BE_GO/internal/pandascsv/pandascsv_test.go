package pandascsv

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"os"
	"slices"
	"testing"
)

// TestColumnsMatchPandas replays the inputs TESTS/adapters/go/pandas_csv_golden.py
// parsed with pandas.read_csv(file, delimiter=';') and compares their columns
// with the ones Read reports (testdata/pandas_headers.json holds only the
// columns, or the exception pandas raises).
func TestColumnsMatchPandas(t *testing.T) {
	raw, err := os.ReadFile("testdata/pandas_headers.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		Pandas string
		Cases  []struct {
			Input   string
			Columns []string
			Error   string
		}
	}
	if err := json.Unmarshal(raw, &golden); err != nil || golden.Pandas != "2.2.3" || len(golden.Cases) < 8000 {
		t.Fatal("invalid golden file", err)
	}
	failures := 0
	for _, tc := range golden.Cases {
		data, _ := base64.StdEncoding.DecodeString(tc.Input)
		frame, err := Read(data)
		var columns []string
		if err == nil {
			columns = frame.Columns
		}
		var ok bool
		switch tc.Error {
		case "empty":
			ok = errors.Is(err, ErrEmpty)
		case "parser":
			ok = errors.Is(err, ErrParser)
		case "decode":
			ok = errors.Is(err, ErrDecode)
		default:
			ok = err == nil && slices.Equal(columns, tc.Columns)
		}
		if !ok {
			failures++
			if failures <= 15 {
				t.Errorf("%q: got %q, %v; pandas %q %s", data, columns, err, tc.Columns, tc.Error)
			}
		}
	}
	if failures > 0 {
		t.Fatalf("%d of %d inputs differ", failures, len(golden.Cases))
	}
	t.Logf("%d inputs", len(golden.Cases))
}

// TestReadHandlesCarriageReturnsAndNUL exercises the inputs the tokenizer's
// blank-line backtrack and the NUL truncation make special; the expectations
// are pandas 2.2.3 (read_csv of the same bytes, from a raw and a decoded
// handle, delimiter=';').
func TestReadHandlesCarriageReturnsAndNUL(t *testing.T) {
	for _, tc := range []struct {
		data    string
		columns []string
	}{
		{"a\rb\r1\r2\r", []string{"a"}},
		{"a\r;b\r", []string{"a"}},
		{"a\r d\r", []string{"a"}},
		{"a;b\r1;2\r", []string{"a", "b"}},
		{"a\x00;b\n1;2\n", []string{"a", "b"}},
		{"a;b\r1;2\r3;4\r", []string{"a", "b"}},
	} {
		frame, err := Read([]byte(tc.data))
		if err != nil || !slices.Equal(frame.Columns, tc.columns) {
			t.Errorf("%q: %q %v, want %q", tc.data, frame.Columns, err, tc.columns)
		}
	}
}
