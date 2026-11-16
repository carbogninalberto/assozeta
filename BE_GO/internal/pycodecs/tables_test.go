package pycodecs

import (
	"bytes"
	"compress/gzip"
	"encoding/hex"
	"encoding/json"
	"io"
	"os"
	"sort"
	"testing"
)

// corpusResult is one recorded bytes.decode result: the decoded text as code
// points, or the UnicodeDecodeError CPython raised.
type corpusResult struct {
	Text  []rune     `json:"t"`
	Error *corpusErr `json:"e"`
}

type corpusErr struct {
	Start  int    `json:"start"`
	End    int    `json:"end"`
	Reason string `json:"reason"`
	Text   string `json:"text"`
}

// corpusCase is one input and the indices of its four results in the pool.
type corpusCase struct {
	In        string `json:"in"`
	Strict    int    `json:"s"`
	Replace   int    `json:"r"`
	Surrogate int    `json:"se"`
	Ignore    int    `json:"g"`
}

type corpusFile struct {
	Seed   int                     `json:"seed"`
	Count  int                     `json:"cases"`
	Pool   []corpusResult          `json:"pool"`
	Codecs map[string][]corpusCase `json:"codecs"`
}

// TestCorpusTables replays testdata/corpus_tables.json.gz, which
// testdata/gen_tables.py records from CPython 3.13.16: for every codec and
// input, the text (or the error) of errors='strict', 'replace',
// 'surrogateescape' and 'ignore' must match exactly. For strict the
// UnicodeDecodeError fields and str(e) are compared too.
func TestCorpusTables(t *testing.T) {
	corpus := readCorpus(t)
	if corpus.Seed == 0 || corpus.Count < 600 || len(corpus.Codecs) == 0 {
		t.Fatalf("corpus header: seed %d, %d cases, %d codecs",
			corpus.Seed, corpus.Count, len(corpus.Codecs))
	}
	names := make([]string, 0, len(corpus.Codecs))
	for name := range corpus.Codecs {
		names = append(names, name)
	}
	sort.Strings(names)
	handlers := []struct {
		name    string
		handler Handler
		index   func(corpusCase) int
	}{
		{"strict", Strict, func(c corpusCase) int { return c.Strict }},
		{"replace", Replace, func(c corpusCase) int { return c.Replace }},
		{"surrogateescape", SurrogateEscape, func(c corpusCase) int { return c.Surrogate }},
		{"ignore", Ignore, func(c corpusCase) int { return c.Ignore }},
	}
	for _, name := range names {
		cases := corpus.Codecs[name]
		// The single-byte codecs (whole tables in singlebyte_gen.go) carry a
		// short corpus; the multibyte ones the full count.
		want := corpus.Count
		if _, single := singleByteCodecs[name]; single {
			want = 60
		}
		if len(cases) < want {
			t.Fatalf("%s: %d cases, want at least %d", name, len(cases), want)
		}
		if !Has(name) {
			t.Fatalf("%s: codec not registered", name)
		}
		for number, test := range cases {
			data, err := hex.DecodeString(test.In)
			if err != nil {
				t.Fatalf("%s case %d: %v", name, number, err)
			}
			for _, entry := range handlers {
				want := corpus.Pool[entry.index(test)]
				out, err, ok := Decode(name, data, entry.handler)
				if !ok {
					t.Fatalf("%s.%s case %d: Decode reported an unknown codec", name, entry.name, number)
				}
				if want.Error != nil {
					decodeErr, typed := err.(*DecodeError)
					if err == nil {
						t.Fatalf("%s.%s case %d (%x): decoded %q, want error %q",
							name, entry.name, number, data, string(out), want.Error.Text)
					}
					if !typed {
						t.Fatalf("%s.%s case %d (%x): error %v, want a *DecodeError",
							name, entry.name, number, data, err)
					}
					if decodeErr.Start != want.Error.Start || decodeErr.End != want.Error.End ||
						decodeErr.Reason != want.Error.Reason {
						t.Fatalf("%s.%s case %d (%x): error %d-%d %q, want %d-%d %q",
							name, entry.name, number, data,
							decodeErr.Start, decodeErr.End, decodeErr.Reason,
							want.Error.Start, want.Error.End, want.Error.Reason)
					}
					if entry.name == "strict" && decodeErr.Error() != want.Error.Text {
						t.Fatalf("%s.%s case %d (%x): error %q, want %q",
							name, entry.name, number, data, decodeErr.Error(), want.Error.Text)
					}
					continue
				}
				if err != nil {
					t.Fatalf("%s.%s case %d (%x): error %v, want %q",
						name, entry.name, number, data, err, string(want.Text))
				}
				if len(out) != len(want.Text) {
					t.Fatalf("%s.%s case %d (%x): decoded %q, want %q",
						name, entry.name, number, data, string(out), string(want.Text))
				}
				for i := range out {
					if out[i] != want.Text[i] {
						t.Fatalf("%s.%s case %d (%x): decoded %q, want %q",
							name, entry.name, number, data, string(out), string(want.Text))
					}
				}
			}
		}
	}
}

func readCorpus(t *testing.T) corpusFile {
	t.Helper()
	file, err := os.Open("testdata/corpus_tables.json.gz")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	compressed, err := gzip.NewReader(file)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := io.ReadAll(compressed)
	if err != nil {
		t.Fatal(err)
	}
	var corpus corpusFile
	if err := json.NewDecoder(bytes.NewReader(raw)).Decode(&corpus); err != nil {
		t.Fatal(err)
	}
	return corpus
}

// TestCJKTableList checks that the codecs cjk.go registers are exactly the
// tries in the embedded blob, so the two lists cannot drift apart.
func TestCJKTableList(t *testing.T) {
	cjkOnce.Do(loadCJKTables)
	fromBlob := make([]string, 0, len(cjkTables))
	for name := range cjkTables {
		fromBlob = append(fromBlob, name)
	}
	sort.Strings(fromBlob)
	registered := append([]string(nil), cjkCodecs...)
	sort.Strings(registered)
	if len(fromBlob) != len(registered) {
		t.Fatalf("blob has %d codecs, cjk.go registers %d", len(fromBlob), len(registered))
	}
	for i := range fromBlob {
		if fromBlob[i] != registered[i] {
			t.Fatalf("blob codec %q, cjk.go registers %q", fromBlob[i], registered[i])
		}
	}
}

// handlerResult is a hand-pinned decode result: text, or an error span.
type handlerResult struct {
	text   []rune
	start  int
	end    int
	reason string
}

// handlerText and handlerError build the expectations of TestHandlerSpans.
func handlerText(cps ...rune) handlerResult { return handlerResult{text: cps} }

func handlerError(start, end int, reason string) handlerResult {
	return handlerResult{start: start, end: end, reason: reason}
}

// TestHandlerSpans pins the error handlers on spans the corpus only samples:
// CPython's surrogateescape escapes at most four bytes of a span and stops at
// the first byte below 0x80, so a sequence cut by ASCII resumes there, while
// 'replace' writes one U+FFFD and 'ignore' drops the whole span. Values come
// from CPython 3.13.16 (bytes.decode(codec, errors)).
func TestHandlerSpans(t *testing.T) {
	illegal, incomplete := cjkIllegal, cjkIncomplete
	cases := []struct {
		codec string
		input string
		want  [4]handlerResult
	}{
		// A lead byte repeated is an illegal one-byte span each time, so
		// surrogateescape escapes every byte separately.
		{"euc_jp", "8f8f8f8f8f", [4]handlerResult{
			handlerError(0, 1, illegal),
			handlerText(0xFFFD, 0xFFFD, 0xFFFD, 0xFFFD),
			handlerText(0xDC8F, 0xDC8F, 0xDC8F, 0xDC8F, 0xDC8F),
			handlerText(),
		}},
		// gb18030's incomplete four-byte form ending in ASCII: the handler
		// escapes only the 0x81 and leaves '0' to decode normally.
		{"gb18030", "8130813081308130813081308130", [4]handlerResult{
			handlerError(12, 14, incomplete),
			handlerText(0x80, 0x80, 0x80, 0xFFFD),
			handlerText(0x80, 0x80, 0x80, 0xDC81, '0'),
			handlerText(0x80, 0x80, 0x80),
		}},
		// A truncated euc_kr make-up form escapes its four leading bytes and
		// then decodes the 0xA4 0x41 ASCII trail as usual.
		{"euc_kr", "a4d4a4a1a4bfa4d4a4d4414141414141", [4]handlerResult{
			handlerError(8, 9, illegal),
			handlerText(0xAC00, 0xFFFD, 0xFFFD, 'A', 'A', 'A', 'A', 'A', 'A'),
			handlerText(0xAC00, 0xDCA4, 0xDCD4, 'A', 'A', 'A', 'A', 'A', 'A'),
			handlerText(0xAC00, 'A', 'A', 'A', 'A', 'A', 'A'),
		}},
		// big5 stops one byte short of a pair at the end of the input.
		{"big5", "aeaeaeaeaeaeaeaeae", [4]handlerResult{
			handlerError(8, 9, incomplete),
			handlerText(0x6085, 0x6085, 0x6085, 0x6085, 0xFFFD),
			handlerText(0x6085, 0x6085, 0x6085, 0x6085, 0xDCAE),
			handlerText(0x6085, 0x6085, 0x6085, 0x6085),
		}},
		// An invalid trail byte is an illegal one-byte span; the ASCII byte is
		// then part of the text under every handler.
		{"shift_jis", "8120812081208120", [4]handlerResult{
			handlerError(0, 1, illegal),
			handlerText(0xFFFD, ' ', 0xFFFD, ' ', 0xFFFD, ' ', 0xFFFD, ' '),
			handlerText(0xDC81, ' ', 0xDC81, ' ', 0xDC81, ' ', 0xDC81, ' '),
			handlerText(' ', ' ', ' ', ' '),
		}},
		// cp932's NEC extension pair decodes, but a trailing lead is incomplete.
		{"cp932", "ed40ed", [4]handlerResult{
			handlerError(2, 3, incomplete),
			handlerText(0x7E8A, 0xFFFD),
			handlerText(0x7E8A, 0xDCED),
			handlerText(0x7E8A),
		}},
	}
	handlers := [4]Handler{Strict, Replace, SurrogateEscape, Ignore}
	for _, test := range cases {
		data, err := hex.DecodeString(test.input)
		if err != nil {
			t.Fatal(err)
		}
		for i, handler := range handlers {
			want := test.want[i]
			out, err, ok := Decode(test.codec, data, handler)
			if !ok {
				t.Fatalf("%s: not registered", test.codec)
			}
			if want.reason != "" {
				decodeErr, typed := err.(*DecodeError)
				if !typed || decodeErr.Start != want.start || decodeErr.End != want.end ||
					decodeErr.Reason != want.reason {
					t.Fatalf("%s %s handler %d: error %v, want %d-%d %q",
						test.codec, test.input, i, err, want.start, want.end, want.reason)
				}
				continue
			}
			if err != nil || len(out) != len(want.text) {
				t.Fatalf("%s %s handler %d: %q, %v, want %q",
					test.codec, test.input, i, string(out), err, string(want.text))
			}
			for j := range out {
				if out[j] != want.text[j] {
					t.Fatalf("%s %s handler %d: %q, want %q",
						test.codec, test.input, i, string(out), string(want.text))
				}
			}
		}
	}
}
