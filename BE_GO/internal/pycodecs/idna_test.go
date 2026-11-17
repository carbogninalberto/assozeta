package pycodecs

// The golden corpora of testdata/gen_idna.py, recorded from CPython 3.13.16:
// Unicode 3.2.0 NFKC, RFC 3491 nameprep as encodings.idna.nameprep applies it,
// the strict bytes.decode("idna") and str.encode("idna") (ToASCII). The
// committed corpora keep every 20th generated case and the whole single code
// point tables; PYCODECS_IDNA_CORPUS_DIR names a directory with the full
// corpora (gen_idna.py --full DIR) to run on those instead.

import (
	"bytes"
	"compress/gzip"
	"encoding/hex"
	"encoding/json"
	"io"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

// idnaCorpusError is one CPython UnicodeEncodeError/UnicodeDecodeError.
type idnaCorpusError struct {
	Start  int    `json:"s"`
	End    int    `json:"e"`
	Reason string `json:"r"`
	Class  string `json:"c"`
}

func readIDNACorpus(t *testing.T, path string, target any) {
	t.Helper()
	if dir := os.Getenv("PYCODECS_IDNA_CORPUS_DIR"); dir != "" {
		path = filepath.Join(dir, filepath.Base(path))
	}
	file, err := os.Open(path)
	if err != nil {
		t.Fatalf("%s: %v", path, err)
	}
	defer file.Close()
	archive, err := gzip.NewReader(file)
	if err != nil {
		t.Fatalf("%s: %v", path, err)
	}
	plain, err := io.ReadAll(archive)
	if err != nil {
		t.Fatalf("%s: %v", path, err)
	}
	if err := json.Unmarshal(plain, target); err != nil {
		t.Fatalf("%s: %v", path, err)
	}
}

// parseCodePoints reads the corpus' "HEX HEX ..." form.
func parseCodePoints(t *testing.T, text string) []rune {
	t.Helper()
	if text == "" {
		return nil
	}
	parts := strings.Split(text, " ")
	out := make([]rune, len(parts))
	for i, part := range parts {
		value, err := strconv.ParseUint(part, 16, 32)
		if err != nil {
			t.Fatalf("%q: %v", text, err)
		}
		out[i] = rune(value)
	}
	return out
}

func runeKey(t *testing.T, key string) rune {
	t.Helper()
	value, err := strconv.ParseUint(key, 10, 32)
	if err != nil {
		t.Fatalf("corpus key %q: %v", key, err)
	}
	return rune(value)
}

// escapedRepr is repr(chr(cp)) for a character repr() escapes.
func escapedRepr(cp int) string {
	switch {
	case cp < 0x100:
		return fmt.Sprintf(`'\x%02x'`, cp)
	case cp < 0x10000:
		return fmt.Sprintf(`'\u%04x'`, cp)
	}
	return fmt.Sprintf(`'\U%08x'`, cp)
}

func sameRunes(a, b []rune) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// showRunes lists code points (a lone surrogate is not a valid Go string).
func showRunes(text []rune) string {
	parts := make([]string, len(text))
	for i, r := range text {
		parts[i] = strconv.FormatInt(int64(r), 16)
	}
	return "[" + strings.Join(parts, " ") + "]"
}

// TestIDNANFKC: what Go calls idnaNFKC must be
// unicodedata.ucd_3_2_0.normalize("NFKC", s) for every single code point and
// for the random 1..8 code point corpus (the 3.2.0 decomposition table and
// corrections, the current combining classes and composition pairs).
func TestIDNANFKC(t *testing.T) {
	var corpus struct {
		Seed    int    `json:"seed"`
		Version string `json:"version"`
		Cases   []struct {
			In  string `json:"i"`
			Out string `json:"o"`
		} `json:"cases"`
		Singles map[string]string `json:"singles"`
	}
	readIDNACorpus(t, "testdata/corpus_idna_nfkc.json.gz", &corpus)
	if corpus.Seed == 0 || len(corpus.Cases) < 10_000 || len(corpus.Singles) == 0 {
		t.Fatalf("corpus header: seed %d, %d cases, %d singles",
			corpus.Seed, len(corpus.Cases), len(corpus.Singles))
	}
	if corpus.Version != idnaTablesOf().version() {
		t.Fatalf("corpus version %q, tables %q", corpus.Version, idnaTablesOf().version())
	}
	singles := make(map[rune][]rune, len(corpus.Singles))
	for key, value := range corpus.Singles {
		singles[runeKey(t, key)] = parseCodePoints(t, value)
	}
	for cp := 0; cp < 0x110000; cp++ {
		r := rune(cp)
		want, changed := singles[r]
		if !changed {
			want = []rune{r}
		}
		got := idnaNFKC([]rune{r})
		if !sameRunes(got, want) {
			t.Fatalf("NFKC U+%04X = %s, want %s", cp, showRunes(got), showRunes(want))
		}
	}
	for number, test := range corpus.Cases {
		got := idnaNFKC(parseCodePoints(t, test.In))
		want := parseCodePoints(t, test.Out)
		if !sameRunes(got, want) {
			t.Fatalf("NFKC case %d %s = %s, want %s",
				number, showRunes(parseCodePoints(t, test.In)), showRunes(got), showRunes(want))
		}
	}
}

// TestIDNANameprep: nameprep is encodings.idna.nameprep on the 3.2.0 database
// for every single code point and for a sample of 100k random multi-character labels,
// including RTL/LTR mixes, mapped-to-nothing characters and prohibited
// characters. The error is CPython's UnicodeEncodeError start, end and reason.
func TestIDNANameprep(t *testing.T) {
	var corpus struct {
		Seed    int                        `json:"seed"`
		Changed map[string]string          `json:"changed"`
		Errors  map[string]idnaCorpusError `json:"errors"`
		// ErrorRanges are code points whose error is
		// "Invalid character " + repr(chr(cp)) in its escaped form.
		ErrorRanges [][2]int `json:"error_ranges"`
		Cases       []struct {
			In  string           `json:"i"`
			Out string           `json:"o"`
			Err *idnaCorpusError `json:"e"`
		} `json:"cases"`
	}
	readIDNACorpus(t, "testdata/corpus_idna_nameprep.json.gz", &corpus)
	if corpus.Seed == 0 || len(corpus.Cases) < 5_000 || len(corpus.Changed) == 0 || len(corpus.Errors) == 0 {
		t.Fatalf("corpus header: seed %d, %d cases, %d changed, %d errors",
			corpus.Seed, len(corpus.Cases), len(corpus.Changed), len(corpus.Errors))
	}
	changed := make(map[rune][]rune, len(corpus.Changed))
	for key, value := range corpus.Changed {
		changed[runeKey(t, key)] = parseCodePoints(t, value)
	}
	failures := make(map[rune]idnaCorpusError, len(corpus.Errors))
	for key, value := range corpus.Errors {
		failures[runeKey(t, key)] = value
	}
	for _, span := range corpus.ErrorRanges {
		for cp := span[0]; cp <= span[1]; cp++ {
			failures[rune(cp)] = idnaCorpusError{Start: 0, End: 1, Reason: "Invalid character " + escapedRepr(cp)}
		}
	}
	for cp := 0; cp < 0x110000; cp++ {
		r := rune(cp)
		got, failure := nameprep([]rune{r})
		if want, refuses := failures[r]; refuses {
			if failure == nil {
				t.Fatalf("nameprep U+%04X = %s, want %q", cp, showRunes(got), want.Reason)
			}
			if failure.start != want.Start || failure.end != want.End || failure.reason != want.Reason {
				t.Fatalf("nameprep U+%04X error %d-%d %q, want %d-%d %q",
					cp, failure.start, failure.end, failure.reason, want.Start, want.End, want.Reason)
			}
			continue
		}
		if failure != nil {
			t.Fatalf("nameprep U+%04X error %d-%d %q, want a result",
				cp, failure.start, failure.end, failure.reason)
		}
		want, differs := changed[r]
		if !differs {
			want = []rune{r}
		}
		if !sameRunes(got, want) {
			t.Fatalf("nameprep U+%04X = %s, want %s", cp, showRunes(got), showRunes(want))
		}
	}
	for number, test := range corpus.Cases {
		got, failure := nameprep(parseCodePoints(t, test.In))
		if test.Err != nil {
			if failure == nil {
				t.Fatalf("nameprep case %d %s = %s, want %q",
					number, test.In, showRunes(got), test.Err.Reason)
			}
			if failure.start != test.Err.Start || failure.end != test.Err.End || failure.reason != test.Err.Reason {
				t.Fatalf("nameprep case %d %s: error %d-%d %q, want %d-%d %q",
					number, test.In, failure.start, failure.end, failure.reason,
					test.Err.Start, test.Err.End, test.Err.Reason)
			}
			continue
		}
		if failure != nil {
			t.Fatalf("nameprep case %d %s: error %d-%d %q, want a result",
				number, test.In, failure.start, failure.end, failure.reason)
		}
		if want := parseCodePoints(t, test.Out); !sameRunes(got, want) {
			t.Fatalf("nameprep case %d %s = %s, want %s", number, test.In, showRunes(got), showRunes(want))
		}
	}
}

// TestIDNADecode: strict bytes.decode("idna") on a sample of 55k inputs (valid
// ACE labels from CPython's own encoder, mixed case, trailing dots, several
// labels, empty labels, non-ASCII bytes, broken punycode, round-trip
// failures, over-long labels, prohibited characters, bidi violations).
func TestIDNADecode(t *testing.T) {
	var corpus struct {
		Seed  int `json:"seed"`
		Cases []struct {
			In  string           `json:"i"`
			Out string           `json:"o"`
			Err *idnaCorpusError `json:"e"`
		} `json:"cases"`
	}
	readIDNACorpus(t, "testdata/corpus_idna_decode.json.gz", &corpus)
	if corpus.Seed == 0 || len(corpus.Cases) < 2_500 {
		t.Fatalf("corpus header: seed %d, %d cases", corpus.Seed, len(corpus.Cases))
	}
	for number, test := range corpus.Cases {
		data, err := hex.DecodeString(test.In)
		if err != nil {
			t.Fatalf("case %d: %v", number, err)
		}
		out, err, ok := Decode("idna", data, Strict)
		if !ok {
			t.Fatalf("case %d: idna is not registered", number)
		}
		if test.Err != nil {
			failure, typed := err.(*DecodeError)
			if !typed {
				t.Fatalf("case %d %x: error %v, want a *DecodeError (%q)",
					number, data, err, test.Err.Reason)
			}
			if failure.Codec != "idna" || !bytes.Equal(failure.Data, data) {
				t.Fatalf("case %d %x: codec %q data %x, want idna %x",
					number, data, failure.Codec, failure.Data, data)
			}
			if failure.Start != test.Err.Start || failure.End != test.Err.End ||
				failure.Reason != test.Err.Reason {
				t.Fatalf("case %d %x: error %d-%d %q, want %d-%d %q",
					number, data, failure.Start, failure.End, failure.Reason,
					test.Err.Start, test.Err.End, test.Err.Reason)
			}
			continue
		}
		if err != nil {
			t.Fatalf("case %d %x: error %v, want %s", number, data, err, test.Out)
		}
		if want := parseCodePoints(t, test.Out); !sameRunes(out, want) {
			t.Fatalf("case %d %x = %s, want %s", number, data, showRunes(out), showRunes(want))
		}
	}
}

// TestIDNAToASCII: the punycode encoder and ToASCII against
// str.encode("idna") (the Go decode path uses them for every ACE label).
func TestIDNAToASCII(t *testing.T) {
	var corpus struct {
		ToASCII []struct {
			In  string           `json:"i"`
			Out string           `json:"b"`
			Err *idnaCorpusError `json:"e"`
		} `json:"toascii"`
	}
	readIDNACorpus(t, "testdata/corpus_idna_decode.json.gz", &corpus)
	if len(corpus.ToASCII) < 1_000 {
		t.Fatalf("%d ToASCII cases", len(corpus.ToASCII))
	}
	tables := idnaTablesOf()
	for number, test := range corpus.ToASCII {
		got, failure := toASCII(tables, parseCodePoints(t, test.In))
		if test.Err != nil {
			if failure == nil {
				t.Fatalf("ToASCII case %d %s = %x, want %q",
					number, test.In, got, test.Err.Reason)
			}
			if failure.start != test.Err.Start || failure.end != test.Err.End ||
				failure.reason != test.Err.Reason {
				t.Fatalf("ToASCII case %d %s: error %d-%d %q, want %d-%d %q",
					number, test.In, failure.start, failure.end, failure.reason,
					test.Err.Start, test.Err.End, test.Err.Reason)
			}
			continue
		}
		if failure != nil {
			t.Fatalf("ToASCII case %d %s: error %d-%d %q, want bytes",
				number, test.In, failure.start, failure.end, failure.reason)
		}
		want, err := hex.DecodeString(test.Out)
		if err != nil {
			t.Fatalf("case %d: %v", number, err)
		}
		if !bytes.Equal(got, want) {
			t.Fatalf("ToASCII case %d %s = %x, want %x", number, test.In, got, want)
		}
	}
}

// TestIDNAStrictOnly: the codec refuses every handler but strict before it
// reads the input (Codec.decode), the same plain UnicodeError as before.
func TestIDNAStrictOnly(t *testing.T) {
	if !Has("idna") {
		t.Fatal("idna is not registered")
	}
	for _, handler := range []Handler{Replace, SurrogateEscape, Ignore, SurrogatePass} {
		_, err, ok := Decode("idna", []byte("xn--bcher-kva"), handler)
		if !ok || err == nil {
			t.Fatalf("handler %d: %v, want the unsupported-handler error", handler, err)
		}
		if want := "Unsupported error handling: " + refusedHandlerName(handler); err.Error() != want {
			t.Fatalf("handler %d: %q, want %q", handler, err, want)
		}
	}
	out, err, ok := Decode("idna", nil, Strict)
	if !ok || err != nil || len(out) != 0 {
		t.Fatalf("empty input: %q, %v", string(out), err)
	}
}
