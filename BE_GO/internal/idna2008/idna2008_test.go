package idna2008

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"
)

// golden mirrors testdata/idna2008_golden.json, which idna2008_golden.py writes with the pinned
// Python interpreter.
type golden struct {
	Unicode   string        `json:"unicode"`
	IDNA      string        `json:"idna"`
	IDNAdata  string        `json:"idnadata"`
	BlockSize int           `json:"block_size"`
	Sweep     []goldenBlock `json:"sweep"`
	Cases     []goldenCase  `json:"cases"`
}

type goldenBlock struct {
	Start  int    `json:"start"`
	SHA256 string `json:"sha256"`
}

type goldenCase struct {
	Label   string `json:"label"`
	Outcome string `json:"outcome"`
}

func loadGolden(t *testing.T) *golden {
	t.Helper()
	raw, err := os.ReadFile("testdata/idna2008_golden.json")
	if err != nil {
		t.Fatalf("read golden file: %v", err)
	}
	var data golden
	if err := json.Unmarshal(raw, &data); err != nil {
		t.Fatalf("parse golden file: %v", err)
	}
	if data.BlockSize != 4096 {
		t.Fatalf("unexpected sweep block size %d", data.BlockSize)
	}
	if len(data.Sweep) == 0 || len(data.Cases) == 0 {
		t.Fatal("golden file has no sweep blocks or no cases")
	}
	if len(data.Sweep)*data.BlockSize != 0x110000 {
		t.Fatalf("%d sweep blocks of %d do not cover every code point", len(data.Sweep), data.BlockSize)
	}
	return &data
}

// encodeOutcome is the golden "outcome" of a label: the A-label text, or "!" for any error.
func encodeOutcome(label string) string {
	encoded, err := Encode(label)
	if err != nil {
		return "!"
	}
	return encoded
}

// blockDigest is the digest idna2008_golden.py computes: the outcomes of chr(cp), "a"+chr(cp) and
// chr(cp)+"a" for every code point of the block in increasing order (surrogates skipped), joined
// with a newline.
func blockDigest(t *testing.T, start, size int) (string, []string) {
	t.Helper()
	var lines []string
	for cp := rune(start); cp < rune(start+size); cp++ {
		if cp >= 0xD800 && cp <= 0xDFFF {
			continue
		}
		character := string(cp)
		lines = append(lines, encodeOutcome(character), encodeOutcome("a"+character), encodeOutcome(character+"a"))
	}
	digest := sha256.Sum256([]byte(strings.Join(lines, "\n")))
	return hex.EncodeToString(digest[:]), lines
}

func TestGoldenSweep(t *testing.T) {
	data := loadGolden(t)
	matched := 0
	for _, block := range data.Sweep {
		digest, lines := blockDigest(t, block.Start, data.BlockSize)
		if digest == block.SHA256 {
			matched++
			continue
		}
		// Print every outcome of the failing block so it can be compared with a Python one-liner.
		position := 0
		for cp := rune(block.Start); cp < rune(block.Start+data.BlockSize); cp++ {
			if cp >= 0xD800 && cp <= 0xDFFF {
				continue
			}
			t.Logf("U+%04X %q %q %q", cp, lines[position], lines[position+1], lines[position+2])
			position += 3
		}
		t.Fatalf("sweep block at U+%04X: got %s, want %s", block.Start, digest, block.SHA256)
	}
	t.Logf("%d/%d sweep blocks match", matched, len(data.Sweep))
}

func TestGoldenCases(t *testing.T) {
	data := loadGolden(t)
	matched := 0
	errorsSeen := 0
	for _, testCase := range data.Cases {
		got := encodeOutcome(testCase.Label)
		if got != testCase.Outcome {
			t.Errorf("Encode(%q) = %q, want %q", testCase.Label, got, testCase.Outcome)
			continue
		}
		matched++
		if testCase.Outcome == "!" {
			errorsSeen++
		}
	}
	t.Logf("%d/%d cases match (%d of them errors)", matched, len(data.Cases), errorsSeen)
}

// decodeGoldenCases are idna.decode from the reference venv (idna 3.20 on CPython 3.13.16): the
// decoded domain, or str(exception) and the IDNAError code for a host idna rejects. Most hosts are
// the lowercased ASCII form httpx.URL.host hands over; the rest keep the uppercase, non-ASCII and
// non-strict separator inputs idna.decode itself also takes.
var decodeGoldenCases = []struct {
	host, decoded, errText, code string
}{
	{"xn--bcher-kva.example", "b\u00fccher.example", "", ""},
	{"xn--fiqs8s.icom.museum", "\u4e2d\u56fd.icom.museum", "", ""},
	{"xn--r8jz45g.xn--zckzah", "\u4f8b\u3048.\u30c6\u30b9\u30c8", "", ""},
	{"xn--bcher-kva.example.", "b\u00fccher.example.", "", ""},
	{"xn--ll-0ea.example", "l\u00b7l.example", "", ""},
	{"xn--ber-goa.example", "\u00fcber.example", "", ""},
	{"xn--1-zhc.example", "\u05d01.example", "", ""},
	{"xn--11b2ezcw70k.example", "\u0915\u094d\u200d\u0937.example", "", ""},
	{"xn--bcher-kva", "b\u00fccher", "", ""},
	{"xn--bcher-kva\u3002example", "b\u00fccher.example", "", ""},
	{"xn--bcher-kva\uff0eexample", "b\u00fccher.example", "", ""},
	{"xn--bcher-kva\uff61example", "b\u00fccher.example", "", ""},
	{"xn--bcher-kva.example\uff0e", "b\u00fccher.example.", "", ""},
	{"XN--BCHER-KVA.EXAMPLE", "b\u00fccher.example", "", ""},
	{"XN--BCHER-KVA.Example", "b\u00fccher.example", "", ""},
	{"example.com", "example.com", "", ""},
	{"xn--bcher-kva.example.5", "b\u00fccher.example.5", "", ""},
	{"xn--bcher-kva.b--d", "b\u00fccher.b--d", "", ""},
	{"xn--zzzzzzzzzz", "\u885e\u7ba5\u7cea\u7e16", "", ""},
	{"xn--", "", "Malformed A-label, no Punycode eligible content found", "invalid_alabel"},
	{"xn--.", "", "Malformed A-label, no Punycode eligible content found", "invalid_alabel"},
	{"xn---", "", "A-label must not end with a hyphen", "invalid_alabel"},
	{"xn--abc-", "", "A-label must not end with a hyphen", "invalid_alabel"},
	{"xn--a-", "", "A-label must not end with a hyphen", "invalid_alabel"},
	{"xn--0", "", "Invalid A-label", "invalid_alabel"},
	{"xn---bbk", "", "A-label is not the canonical Punycode encoding of its U-label", "non_canonical_alabel"},
	{"xn--xn--abc", "", "Codepoint U+0080 at position 1 of '\\x80x\\x80n-\\x80' not allowed", "disallowed_codepoint"},
	{"xn--a", "", "Codepoint U+0080 at position 1 of '\\x80' not allowed", "disallowed_codepoint"},
	{"xn--a.example", "", "Codepoint U+0080 at position 1 of '\\x80' not allowed", "disallowed_codepoint"},
	{"xn--bcher-kva.xn--a", "", "Codepoint U+0080 at position 1 of '\\x80' not allowed", "disallowed_codepoint"},
	{"xn--bcher-kva.my_host", "", "Codepoint U+005F at position 3 of 'my_host' not allowed", "disallowed_codepoint"},
	{"xn--bcher-kva.EX_ample", "", "Codepoint U+005F at position 3 of 'ex_ample' not allowed", "disallowed_codepoint"},
	{"xn--bcher-kva.ab--cd", "", "Label has disallowed hyphens in 3rd and 4th position", "hyphen_3_4"},
	{"xn--bcher-kva.-x", "", "Label must not start or end with a hyphen", "hyphen_start_end"},
	{"xn--bcher-kva.x-", "", "Label must not start or end with a hyphen", "hyphen_start_end"},
	{"xn--bcher-kva..example", "", "Empty Label", "empty_label"},
	{".xn--bcher-kva", "", "Empty Label", "empty_label"},
	{"xn--bcher-kva.example..", "", "Empty Label", "empty_label"},
	{"", "", "Empty domain", "empty_domain"},
	{"xn--bcher-kva." + strings.Repeat("a", 250), "", "Domain too long", "domain_too_long"},
	{"xn--" + strings.Repeat("a", 1021), "", "Domain too long", "input_too_long"},
	{"xn--e-xbb.example", "", "Label must be in Normalization Form C", "not_nfc"},
	{"xn--a-wbb.example", "", "Label begins with an illegal combining character", "leading_combiner"},
	{"xn--ls8h.example", "", "Codepoint U+1F4A9 at position 1 of '\U0001f4a9' not allowed", "disallowed_codepoint"},
	{"xn--ab-9t72a.example", "", "Codepoint U+1F4A9 at position 3 of 'ab\U0001f4a9' not allowed", "disallowed_codepoint"},
	{"xn--a'b-joa.example", "", "Codepoint U+0027 at position 2 of \"a'b\u00fc\" not allowed", "disallowed_codepoint"},
	{"xn--a\\-yka.example", "", "Codepoint U+005C at position 2 of 'a\\\\\u00fc' not allowed", "disallowed_codepoint"},
	{"xn--ab-0ea.example", "", "Codepoint U+00B7 not allowed at position 2 in 'a\u00b7b'", "contexto"},
	{"xn--ab-3n4a.example", "", "Codepoint U+30FB not allowed at position 2 in 'a\u30fbb'", "contexto"},
	{"xn--ab-j1t.example", "", "Joiner U+200C not allowed at position 2 in 'a\\u200cb'", "contextj"},
	{"xn--a-zmc.example", "", "Invalid direction for codepoint at position 2 in a left-to-right label", "bidi_rule_5"},
	{"xn--1-0hc.example", "", "First codepoint in label '1\u05d0' must be directionality L, R or AL", "bidi_rule_1"},
	{"xn--!-zhc.example", "", "Codepoint U+0021 at position 2 of '\u05d0!' not allowed", "disallowed_codepoint"},
	{"xn--a-ymc.example", "", "Invalid direction for codepoint at position 2 in a right-to-left label", "bidi_rule_2"},
	{"xn--4db40amo.example", "", "Codepoint U+0661 not allowed at position 2 in '\u05d0\u0661\u06f1'", "contexto"},
	{"b\u00fccher.example", "b\u00fccher.example", "", ""},
	{"b\u00fccher.example.", "b\u00fccher.example.", "", ""},
	{"B\u00dcCHER.example", "", "Codepoint U+0042 at position 1 of 'B\u00dcCHER' not allowed", "disallowed_codepoint"},
	{"-bad.example", "", "Label must not start or end with a hyphen", "hyphen_start_end"},
	{"ab--cd.example", "", "Label has disallowed hyphens in 3rd and 4th position", "hyphen_3_4"},
	{"xn--bcher-kva\u3002xn--zckzah", "b\u00fccher.\u30c6\u30b9\u30c8", "", ""},
}

// TestDecodeGoldenCases checks Decode against idna.decode: the decoded domain for a host Python
// accepts, and idna's own *Error text and code for one it rejects.
func TestDecodeGoldenCases(t *testing.T) {
	matched := 0
	for _, testCase := range decodeGoldenCases {
		decoded, err := Decode(testCase.host)
		if testCase.errText == "" {
			if err != nil {
				t.Errorf("Decode(%q) error = %v, want %q", testCase.host, err, testCase.decoded)
				continue
			}
			if decoded != testCase.decoded {
				t.Errorf("Decode(%q) = %q, want %q", testCase.host, decoded, testCase.decoded)
				continue
			}
		} else {
			if err == nil {
				t.Errorf("Decode(%q) = %q, want error %q", testCase.host, decoded, testCase.errText)
				continue
			}
			failure, ok := err.(*Error)
			if !ok {
				t.Errorf("Decode(%q) error is %T, want *Error", testCase.host, err)
				continue
			}
			if failure.Text != testCase.errText || failure.Code != testCase.code {
				t.Errorf("Decode(%q) error = %q (%s), want %q (%s)",
					testCase.host, failure.Text, failure.Code, testCase.errText, testCase.code)
				continue
			}
			if !errors.Is(err, ErrIDNA) {
				t.Errorf("Decode(%q) error = %v, want an error wrapping ErrIDNA", testCase.host, err)
				continue
			}
		}
		matched++
	}
	t.Logf("%d/%d decode cases match", matched, len(decodeGoldenCases))
}

func TestEncodeRejectsInvalidUTF8(t *testing.T) {
	for _, label := range []string{"\xff", "a\xc3", "\xed\xa0\x80", "xn--\xff"} {
		if _, err := Encode(label); !errors.Is(err, ErrIDNA) {
			t.Errorf("Encode(%q) error = %v, want an error wrapping ErrIDNA", label, err)
		}
	}
}

func TestEncodeErrorWrapsErrIDNA(t *testing.T) {
	for _, label := range []string{"", "-a", "A\u00fc", "a..b", "\u0301a"} {
		if _, err := Encode(label); !errors.Is(err, ErrIDNA) {
			t.Errorf("Encode(%q) error = %v, want an error wrapping ErrIDNA", label, err)
		}
	}
	if _, err := Encode("b\u00fccher"); err != nil {
		t.Fatalf("Encode(%q) = %v, want no error", "b\u00fccher", err)
	}
}

func TestTablesVersions(t *testing.T) {
	if unicodeVersion != "15.1.0" {
		t.Errorf("unicodeVersion = %q, want %q", unicodeVersion, "15.1.0")
	}
	if idnadataVersion != "18.0.0" {
		t.Errorf("idnadataVersion = %q, want %q", idnadataVersion, "18.0.0")
	}
	data := loadGolden(t)
	if data.Unicode != unicodeVersion || data.IDNAdata != idnadataVersion {
		t.Errorf("golden was written for unicodedata %s / idnadata %s, tables are %s / %s",
			data.Unicode, data.IDNAdata, unicodeVersion, idnadataVersion)
	}
}
