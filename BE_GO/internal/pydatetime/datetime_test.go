package pydatetime

import (
	"encoding/json"
	"fmt"
	"os"
	"testing"
)

// The corpus is generated from Django 5.2 on CPython 3.13 by
// TESTS/adapters/django/pydatetime_corpus.py.
func TestParseDatetimeMatchesDjango(t *testing.T) {
	raw, err := os.ReadFile("testdata/parse_corpus.json")
	if err != nil {
		t.Fatal(err)
	}
	var corpus [][]json.RawMessage
	if err := json.Unmarshal(raw, &corpus); err != nil {
		t.Fatal(err)
	}
	failures := 0
	for _, entry := range corpus {
		var text string
		if err := json.Unmarshal(entry[0], &text); err != nil {
			t.Fatal(err)
		}
		want := string(entry[1])
		got := "null"
		moment, err := ParseDatetime(text)
		switch {
		case err != nil:
			got = `"ValueError"`
		case moment != nil:
			offset, name := "null", "null"
			if moment.Aware {
				offset = fmt.Sprint(moment.Offset)
				name = fmt.Sprintf("%q", moment.ZoneName())
			}
			got = fmt.Sprintf("[%d,%d,%d,%d,%d,%d,%d,%s,%s]", moment.Year, moment.Month, moment.Day, moment.Hour,
				moment.Minute, moment.Second, moment.Micro, offset, name)
		}
		if got != normalize(want) {
			if failures++; failures <= 40 {
				t.Errorf("%q: got %s, want %s", text, got, normalize(want))
			}
		}
	}
	if len(corpus) < 5000 {
		t.Fatalf("corpus has %d cases", len(corpus))
	}
	if failures > 0 {
		t.Errorf("%d of %d cases differ", failures, len(corpus))
	}
}

// normalize rewrites the corpus entry without spaces, and a naive datetime's tzname.
func normalize(raw string) string {
	var out []byte
	inString := false
	for i := 0; i < len(raw); i++ {
		c := raw[i]
		if c == '"' && (i == 0 || raw[i-1] != '\\') {
			inString = !inString
		}
		if !inString && (c == ' ' || c == '\n') {
			continue
		}
		out = append(out, c)
	}
	return string(out)
}

func TestInRomeMatchesPytz(t *testing.T) {
	raw, err := os.ReadFile("testdata/rome_corpus.json")
	if err != nil {
		t.Fatal(err)
	}
	var corpus [][]json.RawMessage
	if err := json.Unmarshal(raw, &corpus); err != nil {
		t.Fatal(err)
	}
	for _, entry := range corpus {
		var text string
		_ = json.Unmarshal(entry[0], &text)
		moment, err := ParseDatetime(text)
		if err != nil || moment == nil {
			t.Fatalf("%q does not parse", text)
		}
		got := `"OverflowError"`
		if local, ok := moment.InRome(); ok {
			got = fmt.Sprintf("[%d,%d,%d,%d,%d,%d,%d]", local.Year(), int(local.Month()), local.Day(), local.Hour(), local.Minute(),
				local.Second(), local.Nanosecond()/1000)
		}
		if want := normalize(string(entry[1])); got != want {
			t.Errorf("%q: got %s, want %s", text, got, want)
		}
	}
}
