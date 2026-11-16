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

// iso2022Result is one recorded bytes.decode result: the decoded text as code
// points, the UnicodeDecodeError CPython raised, or another exception (the
// RuntimeError iso2022_jp_2 raises for a G2 designation its processg2 does not
// implement).
type iso2022Result struct {
	Text  []rune         `json:"t"`
	Error *iso2022Err    `json:"e"`
	Other *iso2022Except `json:"x"`
}

type iso2022Err struct {
	Start  int    `json:"start"`
	End    int    `json:"end"`
	Reason string `json:"reason"`
	Text   string `json:"text"`
}

type iso2022Except struct {
	Type string `json:"type"`
	Text string `json:"text"`
}

// iso2022Case is one input and the indices of its four results in the pool.
type iso2022Case struct {
	In        string `json:"in"`
	Strict    int    `json:"s"`
	Replace   int    `json:"r"`
	Surrogate int    `json:"se"`
	Ignore    int    `json:"g"`
}

type iso2022Corpus struct {
	Seed   int                      `json:"seed"`
	Count  int                      `json:"cases"`
	Pool   []iso2022Result          `json:"pool"`
	Codecs map[string][]iso2022Case `json:"codecs"`
}

// TestCorpusISO2022 replays testdata/corpus_iso2022.json.gz, which
// testdata/gen_iso2022.py records from CPython 3.13.16: for every codec and
// input, the text (or the error) of errors='strict', 'replace',
// 'surrogateescape' and 'ignore' must match exactly. For strict the
// UnicodeDecodeError fields and str(e) are compared too.
func TestCorpusISO2022(t *testing.T) {
	corpus := readISO2022Corpus(t)
	if corpus.Seed == 0 || corpus.Count < 1500 || len(corpus.Codecs) != len(iso2022Codecs)+1 {
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
		index   func(iso2022Case) int
	}{
		{"strict", Strict, func(c iso2022Case) int { return c.Strict }},
		{"replace", Replace, func(c iso2022Case) int { return c.Replace }},
		{"surrogateescape", SurrogateEscape, func(c iso2022Case) int { return c.Surrogate }},
		{"ignore", Ignore, func(c iso2022Case) int { return c.Ignore }},
	}
	for _, name := range names {
		cases := corpus.Codecs[name]
		if len(cases) < corpus.Count {
			t.Fatalf("%s: %d cases, want at least %d", name, len(cases), corpus.Count)
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
				if want.Other != nil {
					if err == nil {
						t.Fatalf("%s.%s case %d (%x): decoded %q, want %s: %s",
							name, entry.name, number, data, string(out),
							want.Other.Type, want.Other.Text)
					}
					if _, typed := err.(*DecodeError); typed {
						t.Fatalf("%s.%s case %d (%x): error %v, want a %s",
							name, entry.name, number, data, err, want.Other.Type)
					}
					if err.Error() != want.Other.Text {
						t.Fatalf("%s.%s case %d (%x): error %q, want %q",
							name, entry.name, number, data, err.Error(), want.Other.Text)
					}
					continue
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

func readISO2022Corpus(t *testing.T) iso2022Corpus {
	t.Helper()
	file, err := os.Open("testdata/corpus_iso2022.json.gz")
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
	var corpus iso2022Corpus
	if err := json.NewDecoder(bytes.NewReader(raw)).Decode(&corpus); err != nil {
		t.Fatal(err)
	}
	return corpus
}

// TestISO2022TableList checks that the codecs iso2022.go registers are exactly
// the configurations in the embedded blob, so the two lists cannot drift.
func TestISO2022TableList(t *testing.T) {
	iso2022Once.Do(loadISO2022Tables)
	fromBlob := make([]string, 0, len(iso2022ByName))
	for name := range iso2022ByName {
		fromBlob = append(fromBlob, name)
	}
	sort.Strings(fromBlob)
	registered := append([]string(nil), iso2022Codecs...)
	sort.Strings(registered)
	if len(fromBlob) != len(registered) {
		t.Fatalf("blob has %d codecs, iso2022.go registers %d", len(fromBlob), len(registered))
	}
	for i := range fromBlob {
		if fromBlob[i] != registered[i] {
			t.Fatalf("blob codec %q, iso2022.go registers %q", fromBlob[i], registered[i])
		}
	}
}

// iso2022HandlerResult is a hand-pinned decode result: text, an error span, or
// another exception CPython raises whatever the handler.
type iso2022HandlerResult struct {
	text   []rune
	start  int
	end    int
	reason string
	other  string
}

// iso2022Text, iso2022Fail and iso2022Other build the expectations of
// TestISO2022Spans.
func iso2022Text(cps ...rune) iso2022HandlerResult { return iso2022HandlerResult{text: cps} }

func iso2022Fail(start, end int, reason string) iso2022HandlerResult {
	return iso2022HandlerResult{start: start, end: end, reason: reason}
}

func iso2022Other(message string) iso2022HandlerResult {
	return iso2022HandlerResult{other: message}
}

// TestISO2022Spans pins the state machine on inputs the corpus only samples:
// the escape sequence lengths CPython reports, the ESC-throughout mode, the
// line-feed shift reset and the G2 single shift. Values come from CPython
// 3.13.16 (bytes.decode(codec, errors)).
func TestISO2022Spans(t *testing.T) {
	illegal, incomplete := cjkIllegal, cjkIncomplete
	cases := []struct {
		codec string
		input string
		want  [4]iso2022HandlerResult
	}{
		// A lone ESC is incomplete.
		{"iso2022_jp", "1b", [4]iso2022HandlerResult{
			iso2022Fail(0, 1, incomplete),
			iso2022Text(0xFFFD),
			iso2022Fail(0, 1, incomplete),
			iso2022Text(),
		}},
		// "ESC (" runs to the end of the input: incomplete over both bytes.
		{"iso2022_jp", "1b28", [4]iso2022HandlerResult{
			iso2022Fail(0, 2, incomplete),
			iso2022Text(0xFFFD),
			iso2022Fail(0, 2, incomplete),
			iso2022Text(),
		}},
		// Fifteen bytes and no escape end is incomplete too (the scan reaches
		// MAX_ESCSEQLEN only when at least sixteen bytes are left).
		{"iso2022_jp", "1b2828282828282828282828282828", [4]iso2022HandlerResult{
			iso2022Fail(0, 15, incomplete),
			iso2022Text(0xFFFD),
			iso2022Fail(0, 15, incomplete),
			iso2022Text(),
		}},
		// A sixteenth graphic byte pushes the scan past MAX_ESCSEQLEN: the ESC
		// alone is illegal and the rest decodes as graphic bytes.
		{"iso2022_jp", "1b282828282828282828282828282828", [4]iso2022HandlerResult{
			iso2022Fail(0, 1, illegal),
			iso2022Text(0xFFFD, '(', '(', '(', '(', '(', '(', '(', '(', '(', '(', '(', '(', '(', '(', '('),
			iso2022Fail(0, 1, illegal),
			iso2022Text('(', '(', '(', '(', '(', '(', '(', '(', '(', '(', '(', '(', '(', '(', '('),
		}},
		// An unknown charset designation is an illegal three-byte span.
		{"iso2022_jp", "1b245a4141", [4]iso2022HandlerResult{
			iso2022Fail(0, 3, illegal),
			iso2022Text(0xFFFD, 'A', 'A'),
			iso2022Fail(0, 3, illegal),
			iso2022Text('A', 'A'),
		}},
		// ESC followed by a byte that is not an escape header switches to
		// ESC-throughout: ISO-8859-1 bytes until an upper-case letter or '@'.
		{"iso2022_jp", "1b8041ff", [4]iso2022HandlerResult{
			iso2022Fail(3, 4, illegal),
			iso2022Text(0x1B, 0x80, 'A', 0xFFFD),
			iso2022Text(0x1B, 0x80, 'A', 0xDCFF),
			iso2022Text(0x1B, 0x80, 'A'),
		}},
		// A line feed resets the shift, so the bytes after it are ASCII.
		{"iso2022_kr", "1b2429430e21210a2121", [4]iso2022HandlerResult{
			iso2022Text(0x3000, 0xA, '!', '!'),
			iso2022Text(0x3000, 0xA, '!', '!'),
			iso2022Text(0x3000, 0xA, '!', '!'),
			iso2022Text(0x3000, 0xA, '!', '!'),
		}},
		// iso2022_kr shifts with SO/SI.
		{"iso2022_kr", "1b2429430e21210f41", [4]iso2022HandlerResult{
			iso2022Text(0x3000, 'A'),
			iso2022Text(0x3000, 'A'),
			iso2022Text(0x3000, 'A'),
			iso2022Text(0x3000, 'A'),
		}},
		// iso2022_jp has NO_SHIFT: SO and SI are copied as C0 characters, so the
		// pair after 0x0E still decodes and the trailing 'A' is incomplete.
		{"iso2022_jp", "1b2428420e21210f41", [4]iso2022HandlerResult{
			iso2022Fail(8, 9, incomplete),
			iso2022Text(0xE, 0x3000, 0xF, 0xFFFD),
			iso2022Fail(8, 9, incomplete),
			iso2022Text(0xE, 0x3000, 0xF),
		}},
		// The G2 single shift of ISO-8859-1, ISO-8859-7 and the default ASCII G2.
		{"iso2022_jp_2", "1b2e411b4e41", [4]iso2022HandlerResult{
			iso2022Text(0xC1),
			iso2022Text(0xC1),
			iso2022Text(0xC1),
			iso2022Text(0xC1),
		}},
		{"iso2022_jp_2", "1b2e461b4e41", [4]iso2022HandlerResult{
			iso2022Text(0x391),
			iso2022Text(0x391),
			iso2022Text(0x391),
			iso2022Text(0x391),
		}},
		{"iso2022_jp_2", "1b4e41", [4]iso2022HandlerResult{
			iso2022Text('A'),
			iso2022Text('A'),
			iso2022Text('A'),
			iso2022Text('A'),
		}},
		{"iso2022_jp_2", "1b4e80", [4]iso2022HandlerResult{
			iso2022Fail(0, 3, illegal),
			iso2022Text(0xFFFD),
			iso2022Fail(0, 3, illegal),
			iso2022Text(),
		}},
		// "ESC . J" designates JIS X 0201 Roman into G2, which processg2 does
		// not implement: CPython raises RuntimeError whatever the handler.
		{"iso2022_jp_2", "1b2e4a1b4e41", [4]iso2022HandlerResult{
			iso2022Other("internal codec error"),
			iso2022Other("internal codec error"),
			iso2022Other("internal codec error"),
			iso2022Other("internal codec error"),
		}},
		// hz: a truncated tilde, and a GB pair split at the end of the input.
		{"hz", "7e", [4]iso2022HandlerResult{
			iso2022Fail(0, 1, incomplete),
			iso2022Text(0xFFFD),
			iso2022Fail(0, 1, incomplete),
			iso2022Text(),
		}},
		{"hz", "7e7b21", [4]iso2022HandlerResult{
			iso2022Fail(2, 3, incomplete),
			iso2022Text(0xFFFD),
			iso2022Fail(2, 3, incomplete),
			iso2022Text(),
		}},
		// In GB mode "~~" is not a tilde: the first '~' is illegal and the
		// second one is then incomplete.
		{"hz", "7e7b21217e7e", [4]iso2022HandlerResult{
			iso2022Fail(4, 5, illegal),
			iso2022Text(0x3000, 0xFFFD, 0xFFFD),
			iso2022Fail(4, 5, illegal),
			iso2022Text(0x3000),
		}},
		// "~~" in ASCII mode is a tilde, so the GB mode is never entered.
		{"hz", "7e7e7b21217e7d", [4]iso2022HandlerResult{
			iso2022Fail(5, 6, illegal),
			iso2022Text('~', '{', '!', '!', 0xFFFD, '}'),
			iso2022Fail(5, 6, illegal),
			iso2022Text('~', '{', '!', '!', '}'),
		}},
		// "~{" in GB mode is illegal; "~}" in ASCII mode is illegal too.
		{"hz", "7e7b7e7b2121", [4]iso2022HandlerResult{
			iso2022Fail(2, 3, illegal),
			iso2022Text(0xFFFD, 0xFFFD, 0x3000),
			iso2022Fail(2, 3, illegal),
			iso2022Text(0x3000),
		}},
		{"hz", "7e7d2121", [4]iso2022HandlerResult{
			iso2022Fail(0, 1, illegal),
			iso2022Text(0xFFFD, '}', '!', '!'),
			iso2022Fail(0, 1, illegal),
			iso2022Text('}', '!', '!'),
		}},
		// "~\n" in ASCII mode is a line continuation.
		{"hz", "7e0a4141", [4]iso2022HandlerResult{
			iso2022Text('A', 'A'),
			iso2022Text('A', 'A'),
			iso2022Text('A', 'A'),
			iso2022Text('A', 'A'),
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
			if want.other != "" {
				if err == nil || err.Error() != want.other {
					t.Fatalf("%s %s handler %d: error %v, want %q",
						test.codec, test.input, i, err, want.other)
				}
				continue
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
