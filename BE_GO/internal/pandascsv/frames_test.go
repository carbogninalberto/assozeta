package pandascsv

import (
	"compress/gzip"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"runtime"
	"slices"
	"testing"
)

type goldenOutcome struct {
	Error   string     `json:"error"`
	Columns []string   `json:"columns"`
	Dtypes  []Dtype    `json:"dtypes"`
	Rows    [][]string `json:"rows"`
	Count   int        `json:"count"`
	Digest  string     `json:"digest"`
}

// tagged is the generator's rendering of a cell: a tag for the Python type,
// then str(value).
func tagged(value Value) string {
	tag := map[string]string{"str": "s", "int": "i", "float": "f", "bool": "b", "numpy.int64": "I", "numpy.uint64": "U",
		"numpy.float64": "F", "numpy.bool": "B"}[value.TypeName()]
	return tag + value.Text()
}

// compare returns "" when the result is pandas' outcome.
func compare(frame *Frame, err error, want *goldenOutcome) string {
	switch {
	case err != nil:
		kind := map[error]string{ErrEmpty: "empty", ErrParser: "parser", ErrDecode: "decode"}[err]
		if kind == "" || kind != want.Error {
			return "error " + err.Error() + ", pandas: " + want.Error
		}
		return ""
	case want.Error != "":
		return "accepted, pandas: " + want.Error
	case !slices.Equal(frame.Columns, want.Columns):
		return "columns differ"
	case !slices.Equal(frame.Dtypes, want.Dtypes):
		return "dtypes differ"
	}
	if want.Digest != "" {
		digest := sha256.New()
		line := make([]byte, 0, 64*len(frame.Columns))
		for _, row := range frame.Rows {
			line = line[:0]
			for _, value := range row {
				line = append(line, tagged(value)...)
				line = append(line, 0x1f)
			}
			line = append(line, 0x1e)
			digest.Write(line)
		}
		if len(frame.Rows) != want.Count || hex.EncodeToString(digest.Sum(nil)) != want.Digest {
			return "rows differ (digest)"
		}
		return ""
	}
	if len(frame.Rows) != len(want.Rows) {
		return "row count differs"
	}
	for index, row := range frame.Rows {
		cells := make([]string, len(row))
		for column, value := range row {
			cells[column] = tagged(value)
		}
		if !slices.Equal(cells, want.Rows[index]) {
			out, _ := json.Marshal(cells)
			expected, _ := json.Marshal(want.Rows[index])
			return "row " + string(out) + ", pandas " + string(expected)
		}
	}
	return ""
}

// TestFramesMatchPandas replays the inputs
// TESTS/adapters/go/pandas_csv_frames_golden.py parsed with
// pandas.read_csv(file, delimiter=';', dtype={column: str, ...}), from an
// S3File (ReadText) and from a decoded binary handle (ReadDecodedText): every
// outcome the package gives must be pandas'. The corpus was recorded with the
// macOS arm64 wheel, whose digit accumulation is fused (floatFMA), so the
// replay reads floats as that wheel on every platform; TestParseFloatPlatform
// pins the platform's own accumulation.
func TestFramesMatchPandas(t *testing.T) {
	platform := runtime.GOOS + "/" + runtime.GOARCH
	saved, had := floatFMA[platform]
	floatFMA[platform] = true
	t.Cleanup(func() {
		if had {
			floatFMA[platform] = saved
		} else {
			delete(floatFMA, platform)
		}
	})
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
		Pandas string
		Cases  []struct {
			Input string
			Texts []string
			Raw   *goldenOutcome
			Text  *goldenOutcome
		}
	}
	if err := json.Unmarshal(raw, &golden); err != nil || golden.Pandas != "2.2.3" || len(golden.Cases) < 45000 {
		t.Fatal("invalid golden file", err)
	}
	failures, frames, typed := 0, 0, 0
	for index, item := range golden.Cases {
		data, err := base64.StdEncoding.DecodeString(item.Input)
		if err != nil {
			t.Fatal(err)
		}
		var textual func(string) bool
		if len(item.Texts) > 0 {
			typed++
			textual = func(column string) bool { return slices.Contains(item.Texts, column) }
		}
		if item.Text == nil {
			item.Text = item.Raw
		}
		for _, run := range []struct {
			name string
			read func([]byte, func(string) bool) (*Frame, error)
			want *goldenOutcome
		}{{"ReadText", ReadText, item.Raw}, {"ReadDecodedText", ReadDecodedText, item.Text}} {
			frame, err := run.read(data, textual)
			if err == nil {
				frames++
			}
			if message := compare(frame, err, run.want); message != "" {
				if failures++; failures <= 25 {
					shown := data
					if len(shown) > 200 {
						shown = shown[:200]
					}
					t.Errorf("case %d %s %q text %q: %s", index, run.name, shown, item.Texts, message)
				}
			}
		}
	}
	if failures > 0 {
		t.Fatalf("%d outcomes differ", failures)
	}
	if typed < 5000 {
		t.Fatalf("only %d cases with text columns", typed)
	}
	t.Logf("%d inputs (%d with text columns): %d frames of %d outcomes", len(golden.Cases), typed, frames,
		2*len(golden.Cases))
}

func TestValueRendering(t *testing.T) {
	for _, tc := range []struct {
		value        Value
		kind, text   string
		truthy, json bool
	}{
		{Value{Kind: NaN}, "float", "nan", true, true},
		{Value{Kind: NaN, NumPy: true}, "numpy.float64", "nan", true, true},
		{Value{Kind: String, S: ""}, "str", "", false, true},
		{Value{Kind: Int, I: -7}, "int", "-7", true, true},
		{Value{Kind: Int, NumPy: true}, "numpy.int64", "0", false, false},
		{Value{Kind: Uint, U: 1 << 63}, "int", "9223372036854775808", true, true},
		{Value{Kind: Uint, U: 1, NumPy: true}, "numpy.uint64", "1", true, false},
		{Value{Kind: Float, F: 1e16, NumPy: true}, "numpy.float64", "1e+16", true, true},
		{Value{Kind: Float, F: 0.00001}, "float", "1e-05", true, true},
		{Value{Kind: Float, F: 0}, "float", "0.0", false, true},
		{Value{Kind: Bool, B: true}, "bool", "True", true, true},
		{Value{Kind: Bool, NumPy: true}, "numpy.bool", "False", false, false},
	} {
		if tc.value.TypeName() != tc.kind || tc.value.Text() != tc.text || tc.value.Truthy() != tc.truthy ||
			tc.value.JSONSerializable() != tc.json {
			t.Errorf("%+v: %s %q %v %v", tc.value, tc.value.TypeName(), tc.value.Text(), tc.value.Truthy(), tc.value.JSONSerializable())
		}
	}
}

func TestReadErrors(t *testing.T) {
	// A whitespace-led line after a bare carriage return reads the lines
	// before it again: here the re-read fills the word buffer pandas starts
	// with, so it raises its ParserError.
	if _, err := Read([]byte("a\rb\rc\r d\r")); !errors.Is(err, ErrParser) {
		t.Errorf("%v", err)
	}
	if _, err := Read([]byte("x\n1\xff\n")); !errors.Is(err, ErrDecode) {
		t.Error(err)
	}
	if frame, err := Read([]byte("x;y\n1;\xff\n")); !errors.Is(err, ErrDecode) {
		t.Error(frame, err)
	}
	// The re-read rows are those pandas buffers when its row limit comes
	// first: the header line, then the lines read again from the start of the
	// tokenize call (pandas 2.2.3: read_csv('a\\r d\\r') has one column 'a'
	// and the rows 'a', ' d').
	frame, err := Read([]byte("a\r d\r"))
	if err != nil || !slices.Equal(frame.Columns, []string{"a"}) || len(frame.Rows) != 2 {
		t.Fatalf("%v %v", frame, err)
	}
	if frame.Rows[1][0].Kind != String || frame.Rows[1][0].S != " d" {
		t.Errorf("%+v", frame.Rows[1])
	}
}
