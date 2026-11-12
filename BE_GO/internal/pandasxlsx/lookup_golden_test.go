package pandasxlsx

import (
	"encoding/base64"
	"encoding/json"
	"math"
	"os"
	"sort"
	"strconv"
	"strings"
	"testing"
)

// The lookup golden is written by TESTS/adapters/go/pandas_xlsx_lookup_golden.py.

type lookupGoldenCase struct {
	Workbook string                     `json:"workbook"`
	Labels   string                     `json:"labels"`
	Outcomes map[string]json.RawMessage `json:"outcomes"`
}

// lookupLabels is json.dumps(columns.tolist()) as the golden records it: text
// quoted, whole numbers as their digits, floats as repr(), nan as NaN.
func lookupLabels(labels []Value) string {
	rendered := make([]string, len(labels))
	for index, label := range labels {
		switch label.Kind {
		case String:
			rendered[index] = strconv.Quote(label.S)
		case Int, Int64:
			rendered[index] = strconv.FormatInt(label.I, 10)
		case BigInt:
			rendered[index] = label.S
		case Uint64:
			rendered[index] = strconv.FormatUint(label.U, 10)
		case Float:
			switch {
			case math.IsNaN(label.F):
				rendered[index] = "NaN"
			case math.IsInf(label.F, 1):
				rendered[index] = "Infinity"
			case math.IsInf(label.F, -1):
				rendered[index] = "-Infinity"
			default:
				rendered[index] = floatRepr(label.F)
			}
		case Bool, NumpyBool:
			rendered[index] = strconv.FormatBool(label.B)
		case NaN:
			rendered[index] = "NaN"
		default:
			rendered[index] = strconv.Quote(label.Str())
		}
	}
	return "[" + strings.Join(rendered, ", ") + "]"
}

// TestPandasLookups requires pandas' labels and its answer for every lookup
// key of every authored header workbook.
func TestPandasLookups(t *testing.T) {
	raw, err := os.ReadFile("testdata/pandas_lookups.json")
	if err != nil {
		t.Fatal(err)
	}
	var corpus struct {
		Cases []lookupGoldenCase `json:"cases"`
	}
	if err = json.Unmarshal(raw, &corpus); err != nil {
		t.Fatal(err)
	}
	if len(corpus.Cases) < 28 {
		t.Fatalf("only %d cases", len(corpus.Cases))
	}
	for index, item := range corpus.Cases {
		data, err := base64.StdEncoding.DecodeString(item.Workbook)
		if err != nil {
			t.Fatalf("case %d: %v", index, err)
		}
		frame, err := ReadAll(data, nil)
		if err != nil {
			t.Fatalf("case %d: %v", index, err)
		}
		if got := lookupLabels(frame.Labels); got != item.Labels {
			t.Errorf("case %d: labels %s, want %s", index, got, item.Labels)
			continue
		}
		keys := make([]string, 0, len(item.Outcomes))
		for text := range item.Outcomes {
			keys = append(keys, text)
		}
		sort.Strings(keys)
		for _, text := range keys {
			decoder := json.NewDecoder(strings.NewReader(text))
			decoder.UseNumber()
			var key any
			if err = decoder.Decode(&key); err != nil {
				t.Fatalf("case %d: key %s: %v", index, text, err)
			}
			position, found, raises, err := frame.Lookup(key)
			if err != nil {
				t.Fatalf("case %d: key %s: %v", index, text, err)
			}
			switch want := string(item.Outcomes[text]); want {
			case `"absent"`:
				if found || raises {
					t.Errorf("case %d: key %s: found %v, raises %v, want absent", index, text, found, raises)
				}
			case `"raises"`:
				if !found || !raises {
					t.Errorf("case %d: key %s: found %v, raises %v, want raises", index, text, found, raises)
				}
			default:
				column, err := strconv.Atoi(want)
				if err != nil {
					t.Fatalf("case %d: outcome %s: %v", index, want, err)
				}
				if !found || raises || position != column {
					t.Errorf("case %d: key %s: position %d, found %v, raises %v, want column %d", index, text, position,
						found, raises, column)
				}
			}
		}
	}
}
