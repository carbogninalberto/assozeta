package pydateutil

import (
	"bufio"
	"compress/gzip"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"
	_ "time/tzdata"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// goldenCase is one line of testdata/corpus.jsonl.gz or testdata/extras.jsonl.gz:
// a call of the real dateutil.parser.parse(text, default=now at midnight) in a
// process with TZ=tz, see testdata/gen_corpus.py and testdata/gen_extras.py.
type goldenCase struct {
	Group   string   `json:"g"`
	Zone    string   `json:"tz"`
	Now     string   `json:"now"`
	Text    string   `json:"in"`
	TextHex string   `json:"inhex"`
	Fields  []int    `json:"ok"`
	Offset  *int64   `json:"off"`
	Name    *string  `json:"name"`
	Str     *string  `json:"str"`
	StrErr  *string  `json:"strerr"`
	Err     []string `json:"err"`
	ErrText string   `json:"errtext"`
	ErrHex  string   `json:"errhex"`
	Refused bool     `json:"refused"`
	// DayFirst: the call passed dayfirst=True.
	DayFirst int `json:"df"`
}

// decode replaces the hex fields of an extras line: text or a message with a
// lone surrogate cannot survive JSON's \udXXX escape (Go decodes an unpaired
// one to U+FFFD), so it is written as the bytes of the Python str instead.
func (c *goldenCase) decode() error {
	if c.TextHex != "" {
		raw, err := hex.DecodeString(c.TextHex)
		if err != nil {
			return err
		}
		c.Text, c.TextHex = string(raw), ""
	}
	if c.ErrHex != "" || c.ErrText != "" {
		message := c.ErrText
		if c.ErrHex != "" {
			raw, err := hex.DecodeString(c.ErrHex)
			if err != nil {
				return err
			}
			message = string(raw)
		}
		if len(c.Err) != 1 {
			return fmt.Errorf("err without exactly one class name: %v", c.Err)
		}
		c.Err = append(c.Err, message)
	}
	return nil
}

func (c goldenCase) check(got Result, err error) string {
	if c.Refused {
		// The only line the port does not answer: a text whose zone name is
		// one of a non-fixed local zone's abbreviations (ErrUnexpectedZone).
		if !errors.Is(err, ErrUnexpectedZone) {
			return "want ErrUnexpectedZone"
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
		t.Fatalf("corpus too small: %d cases (%d values, %d exceptions, %d zone errors)", total, values, raised, refused)
	}
	t.Logf("%d cases (%d with dayfirst=True): %d values, %d exceptions, %d zone errors", total, dayFirst, values, raised, refused)
}

// TestExtrasCorpus replays the long and non-ASCII cases of testdata/extras.jsonl.gz.
func TestExtrasCorpus(t *testing.T) {
	total, values, raised, refused, _ := replayCorpus(t, "testdata/extras.jsonl.gz")
	if total < 25 || values < 4 || raised < 20 || refused != 0 {
		t.Fatalf("extras corpus too small or unexpected: %d cases (%d values, %d exceptions, %d zone errors)",
			total, values, raised, refused)
	}
	t.Logf("%d cases: %d values, %d exceptions", total, values, raised)
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
		if err := c.decode(); err != nil {
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

// Zones the application never passes are application state, never handed off.
func TestZoneError(t *testing.T) {
	now := time.Date(2026, 11, 10, 12, 0, 0, 0, time.UTC)
	rome, err := time.LoadLocation("Europe/Rome")
	if err != nil {
		t.Fatal(err)
	}
	for name, local := range map[string]*time.Location{"nil zone": nil, "local abbreviation": rome} {
		_, err := Parse("10:00 CEST", now, local)
		if !errors.Is(err, ErrUnexpectedZone) || !errors.Is(err, refusal.ErrAppState) || errors.Is(err, refusal.ErrNotPorted) {
			t.Errorf("%s: want ErrUnexpectedZone, got %v", name, err)
		}
	}
}

// TestTextIsAPythonStr covers the two shapes the port used to refuse: text
// holding bytes that are not UTF-8 (Django hands over its errors='replace'
// decoding, a JSON lone surrogate its three-byte form) and text of any length.
func TestTextIsAPythonStr(t *testing.T) {
	now := time.Date(2026, 11, 10, 12, 0, 0, 0, time.UTC)
	for _, item := range []struct {
		name string
		text string
		want string
	}{
		{"replaced byte", "2020-01-03\xff", "Unknown string format: 2020-01-03\ufffd"},
		{"surrogate", "12/03/2020 \xed\xa0\x80", "Unknown string format: 12/03/2020 \xed\xa0\x80"},
		{"two surrogates", "\xed\xa0\xbd\xed\xb8\x80", "Unknown string format: \xed\xa0\xbd\xed\xb8\x80"},
	} {
		var raised *Error
		if _, err := Parse(item.text, now, time.UTC); !errors.As(err, &raised) || raised.Message != item.want {
			t.Errorf("%s: got %v, want %q", item.name, err, item.want)
		}
	}
	// 70000 digits: Python's int conversion for the C int of datetime.replace.
	var raised *Error
	if _, err := Parse(strings.Repeat("1", 70000), now, time.UTC); !errors.As(err, &raised) ||
		raised.Class != ClassOverflowError || raised.Message != "Python int too large to convert to C int" {
		t.Errorf("70000 digits: got %v", err)
	}
}

// A lone surrogate is not a letter, digit, space or decimal digit in Python
// (str.isalpha, .isdigit, .isspace and .isdecimal are all false).
func TestSurrogateCharacterClasses(t *testing.T) {
	for r := rune(0xD800); r <= 0xDFFF; r++ {
		if isAlpha(r) || isDigit(r) || isSpace(r) || decimalValue(r) >= 0 {
			t.Fatalf("U+%04X is a letter, digit, space or decimal digit", r)
		}
		encoded := pystr.String([]rune{r})
		if runeCount(encoded) != 1 || isDigitString(encoded) {
			t.Fatalf("U+%04X: runeCount %d, isDigitString %v", r, runeCount(encoded), isDigitString(encoded))
		}
	}
}

// A 1 MiB text stays linear: a 64 KiB bound and per-byte work would show here.
func TestMebibyteText(t *testing.T) {
	now := time.Date(2026, 11, 10, 12, 0, 0, 0, time.UTC)
	started := time.Now()
	got, err := Parse(strings.Repeat(" ", 1<<20)+"12/03/2020", now, time.UTC)
	if err != nil || (got != Result{Year: 2020, Month: 12, Day: 3}) {
		t.Fatalf("got %+v, %v", got, err)
	}
	word := strings.Repeat("q", 1<<20)
	var raised *Error
	if _, err := Parse(word, now, time.UTC); !errors.As(err, &raised) ||
		raised.Message != "Unknown string format: "+word {
		t.Fatalf("1 MiB word: %v", err)
	}
	// 1,000,001 digits: the oracle needs about 40 s to raise the same
	// OverflowError, so the case is not in the generated corpus.
	if _, err := Parse(strings.Repeat("9", 1000001), now, time.UTC); !errors.As(err, &raised) ||
		raised.Class != ClassOverflowError || raised.Message != "Python int too large to convert to C int" {
		t.Fatalf("1,000,001 digits: %v", err)
	}
	// A linear parse takes about 0.3 s (1.5 s under -race locally); the budget
	// only has to catch a quadratic scan, so the race shards get more room.
	budget := 3 * time.Second
	if raceDetector {
		budget = 15 * time.Second
	}
	if elapsed := time.Since(started); elapsed > budget {
		t.Fatalf("3 MiB of text took %v", elapsed)
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
