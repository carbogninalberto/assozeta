package pydateutil

import (
	"bufio"
	"compress/gzip"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"
	"time"
	_ "time/tzdata"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// goldenCase is one line of testdata/corpus.jsonl.gz: a call of the real
// dateutil.parser.parse(text, default=now at midnight) in a process with
// TZ=tz, see testdata/gen_corpus.py.
type goldenCase struct {
	Group   string   `json:"g"`
	Zone    string   `json:"tz"`
	Now     string   `json:"now"`
	Text    string   `json:"in"`
	Fields  []int    `json:"ok"`
	Offset  *int64   `json:"off"`
	Name    *string  `json:"name"`
	Str     *string  `json:"str"`
	StrErr  *string  `json:"strerr"`
	Err     []string `json:"err"`
	Refused bool     `json:"refused"`
	// DayFirst: the call passed dayfirst=True.
	DayFirst int `json:"df"`
}

func (c goldenCase) check(got Result, err error) string {
	if c.Refused {
		if !errors.Is(err, ErrUnsupported) {
			return "want a refusal"
		}
		return ""
	}
	if c.Err != nil {
		var raised *Error
		if !errors.As(err, &raised) {
			return "want " + c.Err[0] + ": " + c.Err[1]
		}
		if raised.Class != c.Err[0] || raised.Message != c.Err[1] {
			return "want " + c.Err[0] + ": " + c.Err[1] + ", got " + raised.Error()
		}
		return ""
	}
	if err != nil {
		return "unexpected error " + err.Error()
	}
	want := Result{Year: c.Fields[0], Month: c.Fields[1], Day: c.Fields[2], Hour: c.Fields[3], Minute: c.Fields[4],
		Second: c.Fields[5], Microsecond: c.Fields[6]}
	if c.Offset != nil {
		want.HasTZ, want.OffsetSeconds = true, *c.Offset
		if c.Name != nil {
			want.TZName, want.HasTZName = *c.Name, true
		}
	}
	if got != want {
		return "fields differ"
	}
	text, textErr := got.String()
	iso, isoErr := got.ISOFormat()
	if c.StrErr != nil {
		var raised *Error
		if !errors.As(textErr, &raised) || raised.Class != ClassValueError || raised.Message != *c.StrErr ||
			isoErr == nil || isoErr.Error() != textErr.Error() {
			return "str() error differs: want " + *c.StrErr
		}
		return ""
	}
	if textErr != nil || isoErr != nil || text != *c.Str || iso != strings.Replace(*c.Str, " ", "T", 1) {
		return "str() differs: want " + *c.Str + ", got " + text
	}
	return ""
}

func TestGoldenCorpus(t *testing.T) {
	total, values, raised, refused, dayFirst := replayCorpus(t, "testdata/corpus.jsonl.gz")
	if total < 100000 || values < 20000 || raised < 20000 || refused == 0 || dayFirst < 40000 {
		t.Fatalf("corpus too small: %d cases (%d values, %d exceptions, %d refusals)", total, values, raised, refused)
	}
	t.Logf("%d cases (%d with dayfirst=True): %d values, %d exceptions, %d refusals", total, dayFirst, values, raised, refused)
}

// replayCorpus runs every case of a corpus file through Parse and fails on
// any difference.
func replayCorpus(t *testing.T, path string) (total, values, raised, refused, dayFirst int) {
	t.Helper()
	file, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	reader, err := gzip.NewReader(file)
	if err != nil {
		t.Fatal(err)
	}
	zones := map[string]*time.Location{}
	scanner := bufio.NewScanner(reader)
	scanner.Buffer(make([]byte, 1<<20), 1<<24)
	mismatches := 0
	for scanner.Scan() {
		var c goldenCase
		if err := json.Unmarshal(scanner.Bytes(), &c); err != nil {
			t.Fatalf("line %d: %v", total+1, err)
		}
		total++
		zone := zones[c.Zone]
		if zone == nil {
			if zone, err = time.LoadLocation(c.Zone); err != nil {
				t.Fatal(err)
			}
			zones[c.Zone] = zone
		}
		// The fake now is a local wall time, like datetime.now().
		now, err := time.ParseInLocation("2006-01-02T15:04:05.999999", c.Now, zone)
		if err != nil {
			t.Fatal(err)
		}
		switch {
		case c.Refused:
			refused++
		case c.Err != nil:
			raised++
		default:
			values++
		}
		parse := Parse
		if c.DayFirst != 0 {
			parse = ParseDayFirst
			dayFirst++
		}
		got, err := parse(c.Text, now, zone)
		if problem := c.check(got, err); problem != "" {
			mismatches++
			if mismatches <= 15 {
				t.Errorf("[%s tz=%s now=%s dayfirst=%d] %.80q: %.200s (got %+v, %.200v)", c.Group, c.Zone, c.Now, c.DayFirst, c.Text, problem, got, err)
			}
		}
	}
	if err := scanner.Err(); err != nil {
		t.Fatal(err)
	}
	if mismatches != 0 {
		t.Fatalf("%d of %d cases differ from dateutil", mismatches, total)
	}
	return total, values, raised, refused, dayFirst
}

func TestRefusals(t *testing.T) {
	now := time.Date(2026, 11, 10, 12, 0, 0, 0, time.UTC)
	rome, err := time.LoadLocation("Europe/Rome")
	if err != nil {
		t.Fatal(err)
	}
	cases := []struct {
		name  string
		text  string
		local *time.Location
	}{
		{"invalid UTF-8", "2020-01-03\xff", time.UTC},
		{"too long", strings.Repeat("1", MaxTextBytes+1), time.UTC},
		{"nil zone", "2020-01-03", nil},
		{"local abbreviation", "10:00 CEST", rome},
	}
	for _, c := range cases {
		_, err := Parse(c.text, now, c.local)
		if !errors.Is(err, ErrUnsupported) || !errors.Is(err, refusal.ErrNotPorted) {
			t.Errorf("%s: want ErrUnsupported, got %v", c.name, err)
		}
	}
	if _, err := Parse(strings.Repeat("1", MaxTextBytes), now, time.UTC); errors.Is(err, ErrUnsupported) {
		t.Errorf("a text of MaxTextBytes must be answered, got %v", err)
	}
}

// The default date is the local date of now, and the pivot of Parse is its
// year.
func TestNowIsLocal(t *testing.T) {
	rome, err := time.LoadLocation("Europe/Rome")
	if err != nil {
		t.Fatal(err)
	}
	now := time.Date(2024, 12, 31, 23, 30, 0, 0, time.UTC) // 2025-01-01 00:30 in Rome
	got, err := Parse("10:20", now, rome)
	if want := (Result{Year: 2025, Month: 1, Day: 1, Hour: 10, Minute: 20}); err != nil || got != want {
		t.Fatalf("got %+v, %v", got, err)
	}
	for _, text := range []string{"1/2/75", "1/2/74", "Jan of 76", "10:20"} {
		viaParse, errParse := Parse(text, now, rome)
		viaPivot, errPivot := ParseWithPivot(text, now, 2025, rome)
		if viaParse != viaPivot || (errParse == nil) != (errPivot == nil) {
			t.Errorf("%q: Parse %+v, ParseWithPivot %+v", text, viaParse, viaPivot)
		}
	}
	if (&Error{Class: ClassParserError}).IsValueError() != true || (&Error{Class: ClassOverflowError}).IsValueError() {
		t.Error("IsValueError")
	}
}
