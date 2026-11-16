package pandascsv

import (
	"compress/gzip"
	"encoding/base64"
	"encoding/json"
	"io"
	"os"
	"runtime"
	"slices"
	"testing"
)

// TestBacktrackMatchesPandas replays the inputs
// testdata/pandas_backtrack_golden.py parsed with pandas.read_csv(file,
// delimiter=';') on a handle without a mode (ReadText) and on a decoded
// binary handle (ReadDecodedText): a whitespace-led line after a bare
// carriage return makes the tokenizer read buffered lines again, and the
// parser's buffers and the reader's row limit decide whether pandas raises
// its ParserError or answers the rows it read. Every outcome the package
// gives must be pandas'. The corpus was recorded with the macOS arm64 wheel,
// so floats are read as that wheel (fused accumulation) on every platform, as
// TestFramesMatchPandas does. Under -race the row-limit frames (hundreds of
// thousands of rows each) are sampled, one in sixteen, to keep the shard's time.
func TestBacktrackMatchesPandas(t *testing.T) {
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
	file, err := os.Open("testdata/pandas_backtrack.json.gz")
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
	if err := json.Unmarshal(raw, &golden); err != nil || golden.Pandas != "2.2.3" || len(golden.Cases) < 4000 {
		t.Fatal("invalid golden file", err)
	}
	failures, overflows, limits, typed, rowLimits := 0, 0, 0, 0, 0
	for index, item := range golden.Cases {
		if raceDetector && (item.Raw != nil && item.Raw.Count > 0 || item.Text != nil && item.Text.Count > 0) {
			if rowLimits++; rowLimits%16 != 1 {
				continue
			}
		}
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
			if message := compare(frame, err, run.want); message != "" {
				if failures++; failures <= 25 {
					shown := data
					if len(shown) > 200 {
						shown = shown[:200]
					}
					t.Errorf("case %d %s %q text %q: %s", index, run.name, shown, item.Texts, message)
				}
			}
			if run.want.Count > 0 {
				limits++
			}
			if run.want.Error == "parser" {
				overflows++
			}
		}
	}
	if failures > 0 {
		t.Fatalf("%d outcomes differ", failures)
	}
	// The corpus must keep covering both answers of a re-read: the ParserError
	// of a full buffer and the reader's row limit.
	wantLimits := 100
	if raceDetector {
		wantLimits = 5 // one in sixteen sampled
	}
	if overflows < 1000 || limits < wantLimits {
		t.Fatalf("corpus too weak: %d buffer overflows, %d row-limit frames", overflows, limits)
	}
	t.Logf("%d inputs (%d with text columns): %d buffer overflows, %d row-limit frames", len(golden.Cases), typed,
		overflows, limits)
}
