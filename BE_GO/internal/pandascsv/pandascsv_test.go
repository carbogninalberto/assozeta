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
// parsed with pandas.read_csv(file, delimiter=';').
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
	failures, refused := 0, 0
	for _, tc := range golden.Cases {
		data, _ := base64.StdEncoding.DecodeString(tc.Input)
		columns, err := Columns(data)
		if errors.Is(err, ErrUnsupported) {
			refused++ // bare carriage returns: outside the subset
			continue
		}
		var ok bool
		switch tc.Error {
		case "empty":
			ok = errors.Is(err, ErrEmpty)
		case "parser":
			ok = errors.Is(err, ErrParser)
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
	if refused > len(golden.Cases)/2 {
		t.Fatalf("%d of %d inputs refused", refused, len(golden.Cases))
	}
	if failures > 0 {
		t.Fatalf("%d of %d inputs differ", failures, len(golden.Cases))
	}
}

func TestColumnsRefusesUnsupported(t *testing.T) {
	for _, data := range [][]byte{[]byte("a\x00;b\n"), {0xff, ';', 'b'}, []byte("a;b\r1;2\r"), []byte("a\r")} {
		if _, err := Columns(data); !errors.Is(err, ErrUnsupported) {
			t.Errorf("%q: %v", data, err)
		}
	}
}
