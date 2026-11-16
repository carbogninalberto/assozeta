package pandasxlsx

import (
	"bytes"
	"compress/gzip"
	"encoding/json"
	"errors"
	"io"
	"math"
	"os"
	"runtime"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// The golden files are written by TESTS/adapters/go/pandas_xlsx_golden.py.

type goldenCase struct {
	Input    []any        `json:"input"`
	Case     int          `json:"case"`
	Text     []string     `json:"text"`
	Error    string       `json:"error"`
	Other    bool         `json:"other"`
	Labels   [][]string   `json:"labels"`
	Rows     [][][]string `json:"rows"`
	Required bool         `json:"required"`
}

type goldenCorpus struct {
	Blobs []string     `json:"blobs"`
	Cases []goldenCase `json:"cases"`
}

// workbook is the generator's pieces() undone: text with one character per
// byte, and indexes of the shared blobs.
func (c goldenCorpus) workbook(item goldenCase) []byte {
	var data []byte
	for _, piece := range item.Input {
		text, isText := piece.(string)
		if !isText {
			text = c.Blobs[int(piece.(float64))]
		}
		for _, char := range text {
			data = append(data, byte(char))
		}
	}
	return data
}

func golden(t *testing.T, name string, target any) {
	t.Helper()
	file, err := os.Open(name)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	stream, err := gzip.NewReader(file)
	if err != nil {
		t.Fatal(err)
	}
	if err = json.NewDecoder(stream).Decode(target); err != nil {
		t.Fatal(err)
	}
}

// gunzip is a golden file's decompressed bytes, for the tests that read more
// than one value out of it.
func gunzip(raw []byte) ([]byte, error) {
	stream, err := gzip.NewReader(bytes.NewReader(raw))
	if err != nil {
		return nil, err
	}
	defer stream.Close()
	return io.ReadAll(stream)
}

var kindTags = map[Kind]string{NaN: "nan", String: "s", Int: "i", Float: "f", Time: "t", NaT: "nat", BigInt: "i", Int64: "i64",
	Uint64: "u64", Bool: "b", NumpyBool: "nb", TimeOfDay: "time", Timedelta: "td", PyTimedelta: "pytd", Date: "date"}

// rendered is the generator's value(): the Python type, str(), and the truth
// value with whether json.dumps encodes it.
func rendered(value Value) []string {
	flags := "F"
	if value.Truthy() {
		flags = "T"
	}
	if value.JSONSerializable() {
		flags += "J"
	} else {
		flags += "-"
	}
	return []string{kindTags[value.Kind], value.Str(), flags}
}

func sameValue(want, got []string) bool {
	return len(want) == 3 && want[0] == got[0] && want[1] == got[1] && want[2] == got[2]
}

// refused reports a workbook pandas reads and the package does not model
// (ErrWorkbookNotPorted).
func refused(err error) bool { return errors.Is(err, ErrWorkbookNotPorted) }

// compare requires pandas' outcome for every reading the package accepts,
// and acceptance of the readings marked required. ReadText gives the same
// frame as ReadAll.
func compare(t *testing.T, corpus goldenCorpus, readings []goldenCase) {
	accepted, frames, failures := 0, 0, 0
	for index, item := range readings {
		data := corpus.workbook(corpus.Cases[item.Case])
		var textual func(string) bool
		if item.Text != nil {
			names := map[string]bool{}
			for _, name := range item.Text {
				names[name] = true
			}
			textual = func(column string) bool { return names[column] }
		}
		if item.Error == "" && !item.Other {
			frames++
		}
		frame, err := ReadAll(data, textual)
		fail := func(format string, args ...any) {
			if failures++; failures <= 25 {
				t.Errorf("reading %d (case %d, text %q): "+format, append([]any{index, item.Case, item.Text}, args...)...)
			}
		}
		narrow, narrowErr := ReadText(data, textual)
		switch {
		case refused(err):
			if item.Required {
				fail("required workbook refused")
			}
			if !refused(narrowErr) {
				fail("ReadText: %v", narrowErr)
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
		if item.Error != "" || item.Other {
			fail("accepted, pandas: %s", item.Error)
			continue
		}
		if len(frame.Labels) != len(item.Labels) || len(frame.Columns) != len(item.Labels) || len(frame.Rows) != len(item.Rows) {
			fail("shape %v x %d, want %v x %d", frame.Columns, len(frame.Rows), item.Labels, len(item.Rows))
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
		if narrowErr != nil {
			fail("ReadText refuses a frame ReadAll reads: %v", narrowErr)
			continue
		}
		if len(narrow.Rows) != len(frame.Rows) || len(narrow.Columns) != len(frame.Columns) {
			fail("ReadText: another shape")
			continue
		}
		for row, values := range narrow.Rows {
			for column, value := range values {
				full := frame.Rows[row][column]
				if value.Kind != full.Kind || value.Str() != full.Str() {
					fail("ReadText row %d column %d: %q, ReadAll %q", row, column, rendered(value), rendered(full))
				}
			}
		}
	}
	t.Logf("%d of %d readings accepted (%d of the %d pandas reads)", accepted, len(readings), accepted, frames)
	if accepted*100 < frames*97 {
		t.Fatalf("only %d of the %d readings pandas makes are accepted", accepted, frames)
	}
}

// TestPandasFrames reads every workbook without converters.
func TestPandasFrames(t *testing.T) {
	var corpus goldenCorpus
	golden(t, "testdata/pandas_frames.json.gz", &corpus)
	if len(corpus.Cases) < 6000 {
		t.Fatalf("only %d cases", len(corpus.Cases))
	}
	replayFused(t)
	readings := make([]goldenCase, len(corpus.Cases))
	for index, item := range corpus.Cases {
		readings[index] = item
		readings[index].Case, readings[index].Text = index, nil
	}
	compare(t, corpus, readings)
}

// TestPandasTextFrames reads the workbooks pandas reads with the text
// converter on the columns each reading names.
func TestPandasTextFrames(t *testing.T) {
	var corpus, texts goldenCorpus
	golden(t, "testdata/pandas_frames.json.gz", &corpus)
	golden(t, "testdata/pandas_text_frames.json.gz", &texts)
	if len(texts.Cases) < 6000 {
		t.Fatalf("only %d readings", len(texts.Cases))
	}
	replayFused(t)
	for index := range texts.Cases {
		if texts.Cases[index].Text == nil {
			texts.Cases[index].Text = []string{}
		}
	}
	compare(t, corpus, texts.Cases)
}

// TestNumericTexts requires maybe_convert_numeric's reading of single texts:
// an int, a float or an error, and the float the text is in a float column.
func TestNumericTexts(t *testing.T) {
	var corpus struct {
		Cases [][]string `json:"cases"`
	}
	golden(t, "testdata/numeric_texts.json.gz", &corpus)
	if len(corpus.Cases) < 8000 {
		t.Fatalf("only %d cases", len(corpus.Cases))
	}
	replayFused(t)
	failures := 0
	for _, item := range corpus.Cases {
		text, kind, want, float := item[0], item[1], item[2], item[3]
		number, ok, err := numericText(text)
		got, gotText, gotFloat := "error", "", ""
		switch {
		case err != nil:
			failures++
			if failures <= 25 {
				t.Errorf("%q: %v", text, err)
			}
			continue
		case ok && number.integer:
			got, gotText, gotFloat = "int", number.i.String(), floatRepr(number.f)
		case ok:
			got, gotText, gotFloat = "float", floatRepr(number.f), floatRepr(number.f)
		}
		if got != kind || gotText != want || gotFloat != float {
			if failures++; failures <= 25 {
				t.Errorf("%q: %s %q (float %q), want %s %q (float %q)", text, got, gotText, gotFloat, kind, want, float)
			}
		}
	}
	if failures > 0 {
		t.Fatalf("%d of %d texts differ", failures, len(corpus.Cases))
	}
}

// replayFused reads float texts as the wheel that recorded the corpora does:
// the macOS arm64 build contracts the digit accumulation into a fused
// multiply-add (floatAccumulation). TestPreciseStrtodPlatform pins the
// platform's own rule.
func replayFused(t *testing.T) {
	t.Helper()
	platform := runtime.GOOS + "/" + runtime.GOARCH
	saved, had := floatAccumulation[platform]
	floatAccumulation[platform] = true
	t.Cleanup(func() {
		if had {
			floatAccumulation[platform] = saved
		} else {
			delete(floatAccumulation, platform)
		}
	})
}

// TestPreciseStrtodPlatform pins the digit accumulation to the pandas wheel of
// the platform: measured with pandas 2.2.3, '3.14159265358979323846' is
// 0x400921fb54442d18 where the compiler contracts 'number * 10 + digit' into a
// fused multiply-add (macOS arm64) and 0x400921fb54442d17 where it does not
// (Linux x86_64 and aarch64). See floatAccumulation.
func TestPreciseStrtodPlatform(t *testing.T) {
	const text = "3.14159265358979323846"
	fused, plain := 0x1.921fb54442d18p+01, 0x1.921fb54442d17p+01
	contracted, measured := floatAccumulation[runtime.GOOS+"/"+runtime.GOARCH]
	value, _, ok, err := strtod(text)
	switch {
	case !measured:
		if !errors.Is(err, refusal.ErrAppState) {
			t.Fatalf("%q: %v %v, want an application state", text, value, err)
		}
	case err != nil || !ok:
		t.Fatalf("%q: %v %v", text, value, err)
	case contracted && value != fused:
		t.Errorf("%q: %x, want the fused %x", text, math.Float64bits(value), math.Float64bits(fused))
	case !contracted && value != plain:
		t.Errorf("%q: %x, want the plain %x", text, math.Float64bits(value), math.Float64bits(plain))
	}
	// The Linux images answer the plain value (pandas 2.2.3 on x86_64 and
	// aarch64: 0x400921fb54442d17), which the rule above selects there.
	if plain != 0x1.921fb54442d17p+01 {
		t.Fatalf("plain %x", plain)
	}
	// A value both accumulations agree on never depends on the platform.
	if number, _, ok, err := strtod("12.5"); err != nil || !ok || number != 12.5 {
		t.Fatalf("12.5: %v %v %v", number, ok, err)
	}
}
