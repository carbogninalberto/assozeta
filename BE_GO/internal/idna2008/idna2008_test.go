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
