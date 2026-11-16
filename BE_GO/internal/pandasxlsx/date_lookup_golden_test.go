package pandasxlsx

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"sort"
	"strconv"
	"strings"
	"testing"
)

// The date lookup golden is written by TESTS/adapters/go/pandas_xlsx_golden.py
// (date_lookups): pandas' labels and its answer for every text key of a date
// or duration header.

type dateLookupCase struct {
	Workbook string                     `json:"workbook"`
	Labels   string                     `json:"labels"`
	Outcomes map[string]json.RawMessage `json:"outcomes"`
}

// TestDateLookups requires pandas' labels and its answer for every key of
// every authored date and duration header workbook: the position of an exact
// label, a partial date slice (a Series, which the importer cannot read) or no
// label at all.
func TestDateLookups(t *testing.T) {
	raw, err := os.ReadFile("testdata/pandas_date_lookups.json.gz")
	if err != nil {
		t.Fatal(err)
	}
	corpus, err := gunzip(raw)
	if err != nil {
		t.Fatal(err)
	}
	var cases struct {
		Platform string           `json:"platform"`
		Cases    []dateLookupCase `json:"cases"`
	}
	if err = json.Unmarshal(corpus, &cases); err != nil {
		t.Fatal(err)
	}
	if len(cases.Cases) < 8 {
		t.Fatalf("only %d cases", len(cases.Cases))
	}
	for index, item := range cases.Cases {
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
			case `"raises"`, `"series"`:
				if !found || !raises {
					t.Errorf("case %d: key %s: found %v, raises %v, want %s", index, text, found, raises, want)
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
