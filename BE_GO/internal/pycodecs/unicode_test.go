package pycodecs

// unicode_test.go replays testdata/corpus_unicode.json.gz, which is generated
// by testdata/gen_unicode.py from the reference CPython 3.13. For every input
// and every recorded error handler it checks the decoded code points, and for
// strict errors the UnicodeDecodeError fields and text (or the plain
// UnicodeError text raised by codecs.decode).

import (
	"compress/gzip"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"testing"
)

// w1bCase is one input and the Python result under each handler.
type w1bCase struct {
	Data    string               `json:"data"` // hex
	Results map[string]w1bResult `json:"r"`
}

// w1bResult is one codecs.decode result:
//
//	kind "text":    CodePoints
//	kind "decode":  Encoding, Start, End, Reason and Text (str(exception))
//	kind "unicode": Text (a plain UnicodeError)
type w1bResult struct {
	Kind       string `json:"kind"`
	CodePoints []int  `json:"cp"`
	Encoding   string `json:"encoding"`
	Start      int    `json:"start"`
	End        int    `json:"end"`
	Reason     string `json:"reason"`
	Text       string `json:"text"`
}

var w1bHandlers = map[string]Handler{
	"strict":          Strict,
	"replace":         Replace,
	"surrogateescape": SurrogateEscape,
	"ignore":          Ignore,
}

func loadW1BCorpus(t *testing.T) map[string][]w1bCase {
	t.Helper()
	f, err := os.Open("testdata/corpus_unicode.json.gz")
	if err != nil {
		t.Fatalf("open corpus: %v", err)
	}
	defer f.Close()
	zr, err := gzip.NewReader(f)
	if err != nil {
		t.Fatalf("corpus gzip: %v", err)
	}
	defer zr.Close()
	raw, err := io.ReadAll(zr)
	if err != nil {
		t.Fatalf("read corpus: %v", err)
	}
	var corpus map[string][]w1bCase
	if err := json.Unmarshal(raw, &corpus); err != nil {
		t.Fatalf("corpus json: %v", err)
	}
	return corpus
}

func w1bCodePoints(runes []rune) []int {
	out := make([]int, len(runes))
	for i, r := range runes {
		out[i] = int(r)
	}
	return out
}

func w1bEqualCodePoints(a, b []int) bool {
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

// TestUnicodeCorpus is the end-to-end replay. A failure names the codec, the
// handler, the input bytes and the first differing code point or field.
func TestUnicodeCorpus(t *testing.T) {
	corpus := loadW1BCorpus(t)
	if len(corpus) == 0 {
		t.Fatal("empty corpus")
	}
	for codec, cases := range corpus {
		codec, cases := codec, cases
		t.Run(codec, func(t *testing.T) {
			if !Has(codec) {
				t.Fatalf("codec %q is not registered", codec)
			}
			if len(cases) < 2000 {
				t.Fatalf("corpus for %q has %d inputs, want at least 2000", codec, len(cases))
			}
			for ci, c := range cases {
				data, err := hex.DecodeString(c.Data)
				if err != nil {
					t.Fatalf("case %d: data hex: %v", ci, err)
				}
				for handlerName, want := range c.Results {
					handler, ok := w1bHandlers[handlerName]
					if !ok {
						t.Fatalf("case %d: unknown handler %q", ci, handlerName)
					}
					out, gotErr, ok := Decode(codec, data, handler)
					if !ok {
						t.Fatalf("codec %q not carried", codec)
					}
					where := fmt.Sprintf("case %d, handler %s, data %x", ci, handlerName, data)
					switch want.Kind {
					case "text":
						if gotErr != nil {
							t.Fatalf("%s: got error %v (%T), want text", where, gotErr, gotErr)
						}
						if got := w1bCodePoints(out); !w1bEqualCodePoints(got, want.CodePoints) {
							t.Fatalf("%s: got code points %v, want %v", where, got, want.CodePoints)
						}
					case "decode":
						de, isDecodeError := gotErr.(*DecodeError)
						if !isDecodeError {
							t.Fatalf("%s: got error %v (%T), want *DecodeError", where, gotErr, gotErr)
						}
						if de.Codec != want.Encoding {
							t.Fatalf("%s: codec %q, want %q", where, de.Codec, want.Encoding)
						}
						if de.Start != want.Start || de.End != want.End {
							t.Fatalf("%s: span %d-%d, want %d-%d (%s)",
								where, de.Start, de.End, want.Start, want.End, want.Reason)
						}
						if de.Reason != want.Reason {
							t.Fatalf("%s: reason %q, want %q", where, de.Reason, want.Reason)
						}
						if got := gotErr.Error(); got != want.Text {
							t.Fatalf("%s: error text\n got: %s\nwant: %s", where, got, want.Text)
						}
					case "unicode":
						if gotErr == nil {
							t.Fatalf("%s: got no error, want UnicodeError(%q)", where, want.Text)
						}
						if _, isDecodeError := gotErr.(*DecodeError); isDecodeError {
							t.Fatalf("%s: got *DecodeError, want plain UnicodeError(%q)", where, want.Text)
						}
						if got := gotErr.Error(); got != want.Text {
							t.Fatalf("%s: error text\n got: %s\nwant: %s", where, got, want.Text)
						}
					default:
						t.Fatalf("case %d: unknown result kind %q", ci, want.Kind)
					}
				}
			}
		})
	}
}

// TestUnicodeCodecNames pins the registered names (codecs.lookup(...).name)
// and the two inherent non-corpus behaviours: idna is strict-only (it does
// decode strict names) and undefined always raises the same plain
// UnicodeError.
func TestUnicodeCodecNames(t *testing.T) {
	for _, name := range []string{
		"utf-8", "utf-8-sig",
		"utf-16", "utf-16-le", "utf-16-be",
		"utf-32", "utf-32-le", "utf-32-be",
		"utf-7", "unicode-escape", "raw-unicode-escape",
		"punycode", "idna", "undefined",
	} {
		if !Has(name) {
			t.Errorf("codec %q is not registered", name)
		}
	}

	if out, err, ok := Decode("idna", []byte("example.com"), Strict); !ok || err != nil ||
		string(out) != "example.com" {
		t.Errorf("idna strict decode: got %q, %v, ok %v", string(out), err, ok)
	}
	if _, err, _ := Decode("idna", nil, Replace); err == nil ||
		err.Error() != "Unsupported error handling: replace" {
		t.Errorf("idna replace decode: got %v", err)
	}
	if _, err, _ := Decode("undefined", nil, Ignore); err == nil ||
		err.Error() != "undefined encoding" {
		t.Errorf("undefined decode: got %v", err)
	}
}
