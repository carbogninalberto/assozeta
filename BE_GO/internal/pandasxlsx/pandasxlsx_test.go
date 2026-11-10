package pandasxlsx

import (
	"compress/gzip"
	"encoding/json"
	"errors"
	"os"
	"testing"
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

// compare requires pandas' outcome for every reading the package accepts,
// and acceptance of the readings marked required. ReadText gives the same
// frame as ReadAll (a numpy.int64 as an Int) or refuses.
func compare(t *testing.T, corpus goldenCorpus, readings []goldenCase) {
	accepted, frames, narrowed, failures := 0, 0, 0, 0
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
		case errors.Is(err, ErrUnsupported):
			if item.Required {
				fail("required workbook refused")
			}
			if !errors.Is(narrowErr, ErrUnsupported) {
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
		plain := true
		for column, want := range item.Labels {
			if got := rendered(frame.Labels[column]); !sameValue(want, got) || frame.Columns[column] != want[1] {
				fail("label %d: %q (%q), want %q", column, got, frame.Columns[column], want)
			}
			plain = plain && want[0] == "s"
		}
		for row, values := range item.Rows {
			for column, want := range values {
				if got := rendered(frame.Rows[row][column]); !sameValue(want, got) {
					fail("row %d column %d: %q, want %q", row, column, got, want)
				}
				switch want[0] {
				case "nan", "s", "i", "i64", "t", "nat":
					plain = plain && frame.Rows[row][column].Kind != BigInt
				case "f":
					plain = plain && want[1] != "inf" && want[1] != "-inf"
				default:
					plain = false
				}
			}
		}
		switch {
		case plain && narrowErr != nil:
			fail("ReadText refuses a frame of its kinds: %v", narrowErr)
		case !plain && !errors.Is(narrowErr, ErrUnsupported):
			fail("ReadText accepts a frame beyond its kinds")
		case plain:
			narrowed++
			if len(narrow.Rows) != len(frame.Rows) || len(narrow.Columns) != len(frame.Columns) {
				fail("ReadText: another shape")
				continue
			}
			for row, values := range narrow.Rows {
				for column, value := range values {
					full := frame.Rows[row][column]
					if full.Kind == Int64 {
						full.Kind = Int
					}
					if value.Kind != full.Kind || value.Str() != full.Str() {
						fail("ReadText row %d column %d: %q, ReadAll %q", row, column, rendered(value), rendered(full))
					}
				}
			}
		}
	}
	t.Logf("%d of %d readings accepted (%d of the %d pandas reads); ReadText accepts %d", accepted, len(readings), accepted, frames, narrowed)
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
	refused, failures := 0, 0
	for _, item := range corpus.Cases {
		text, kind, want, float := item[0], item[1], item[2], item[3]
		number, ok, err := numericText(text)
		got, gotText, gotFloat := "error", "", ""
		switch {
		case err != nil:
			refused++
			continue
		case ok && number.integer:
			got, gotText, gotFloat = "int", number.i.String(), floatRepr(number.f)
		case ok:
			got, gotText, gotFloat = "float", floatRepr(number.f), floatRepr(number.f)
		}
		if ok && number.uncertain {
			// The float is refused where a column needs it; the integer is exact.
			refused++
			if !number.integer {
				continue
			}
			gotFloat = float
		}
		if got != kind || gotText != want || gotFloat != float {
			if failures++; failures <= 25 {
				t.Errorf("%q: %s %q (float %q), want %s %q (float %q)", text, got, gotText, gotFloat, kind, want, float)
			}
		}
	}
	t.Logf("%d of %d texts refused", refused, len(corpus.Cases))
	if refused*10 > len(corpus.Cases) {
		t.Fatalf("%d of %d texts refused", refused, len(corpus.Cases))
	}
}
