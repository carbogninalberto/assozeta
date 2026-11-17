package pilimage

import (
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"testing"
)

type pyError struct {
	Class string `json:"class"`
	Text  string `json:"text"`
}

type corpusOpen struct {
	Format string   `json:"format"`
	Mode   string   `json:"mode"`
	Width  int      `json:"width"`
	Height int      `json:"height"`
	Error  *pyError `json:"error"`
}

type corpusRGB struct {
	Mode  string   `json:"mode"`
	Data  string   `json:"data"`
	Alpha *string  `json:"alpha"`
	Error *pyError `json:"error"`
}

type corpusTransparent struct {
	Value *string  `json:"value"`
	Error *pyError `json:"error"`
}

type corpusCase struct {
	Data        string             `json:"data"`
	Open        corpusOpen         `json:"open"`
	RGB         *corpusRGB         `json:"rgb"`
	Transparent *corpusTransparent `json:"transparent"`
}

type corpusDoc struct {
	Pillow string       `json:"pillow"`
	Cases  []corpusCase `json:"cases"`
}

func readCorpus(t *testing.T) corpusDoc {
	t.Helper()
	file, err := os.Open("testdata/corpus.json.gz")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	zr, err := gzip.NewReader(file)
	if err != nil {
		t.Fatal(err)
	}
	var doc corpusDoc
	if err := json.NewDecoder(zr).Decode(&doc); err != nil {
		t.Fatal(err)
	}
	return doc
}

func sha(b []byte) string {
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:])
}

func matchesPython(t *testing.T, label string, got error, want *pyError) {
	t.Helper()
	if want == nil {
		if got != nil {
			t.Fatalf("%s: unexpected error %v", label, got)
		}
		return
	}
	if got == nil {
		t.Fatalf("%s: want %s %q, got success", label, want.Class, want.Text)
	}
	var pe *Error
	if !errors.As(got, &pe) {
		t.Fatalf("%s: want %s %q, got %v", label, want.Class, want.Text, got)
	}
	if pe.Class != want.Class || pe.Text != want.Text {
		t.Fatalf("%s: want %s %q, got %s %q", label, want.Class, want.Text, pe.Class, pe.Text)
	}
}

var decodedFormats = map[string]bool{
	"PNG": true, "GIF": true, "BMP": true, "DIB": true, "PPM": true,
}

// TestCorpusMatchesPillow replays every corpus case and requires the same
// answer Pillow/reportlab gave (TESTS/fixtures/generate_pilimage_corpus.py).
func TestCorpusMatchesPillow(t *testing.T) {
	doc := readCorpus(t)
	if len(doc.Cases) < 1500 {
		t.Fatalf("only %d corpus cases", len(doc.Cases))
	}
	notPorted := map[string]int{}
	decoded := map[string]int{}
	for i, c := range doc.Cases {
		caseIndex = i
		caseData = c.Data
		data, err := base64.StdEncoding.DecodeString(c.Data)
		if err != nil {
			t.Fatalf("case %d: %v", i, err)
		}
		im, err := safeOpen(data)
		if c.Open.Error != nil {
			matchesPython(t, fmt.Sprintf("case %d open", i), err, c.Open.Error)
			continue
		}
		if err != nil {
			if errors.Is(err, ErrFormatNotPorted) {
				if decodedFormats[c.Open.Format] {
					t.Fatalf("case %d: ErrFormatNotPorted for decoded format %s", i, c.Open.Format)
				}
				notPorted[c.Open.Format]++
				continue
			}
			t.Fatalf("case %d: want %s %s %dx%d, got %v (data %s)",
				i, c.Open.Format, c.Open.Mode, c.Open.Width, c.Open.Height, err, shortData(data))
		}
		if im.Format != c.Open.Format || im.Mode != c.Open.Mode || im.Width != c.Open.Width || im.Height != c.Open.Height {
			t.Fatalf("case %d: want %s %s %dx%d, got %s %s %dx%d",
				i, c.Open.Format, c.Open.Mode, c.Open.Width, c.Open.Height, im.Format, im.Mode, im.Width, im.Height)
		}
		if c.RGB != nil {
			decoded[im.Format]++
			mode, rgb, alpha, err := safeRGBData(im)
			if c.RGB.Error != nil {
				matchesPython(t, fmt.Sprintf("case %d rgb", i), err, c.RGB.Error)
			} else {
				if err != nil {
					t.Fatalf("case %d rgb: %v (data %s)", i, err, shortData(data))
				}
				if mode != c.RGB.Mode {
					t.Fatalf("case %d rgb: want mode %s, got %s", i, c.RGB.Mode, mode)
				}
				if got, want := sha(rgb), c.RGB.Data; got != want {
					t.Fatalf("case %d rgb: data sha %s, want %s (data %s)", i, got, want, shortData(data))
				}
				if c.RGB.Alpha == nil {
					if alpha != nil {
						t.Fatalf("case %d rgb: unexpected alpha band", i)
					}
				} else {
					if alpha == nil {
						t.Fatalf("case %d rgb: missing alpha band", i)
					}
					if got, want := sha(alpha), *c.RGB.Alpha; got != want {
						t.Fatalf("case %d rgb: alpha sha %s, want %s", i, got, want)
					}
				}
			}
		} else if im.Format == "JPEG" {
			if _, _, _, err := im.RGBData(); !errors.Is(err, ErrFormatNotPorted) {
				t.Fatalf("case %d: JPEG RGBData = %v, want ErrFormatNotPorted", i, err)
			}
		}
		if c.Transparent != nil {
			got := im.Transparent()
			if c.Transparent.Error != nil {
				// getTransparent raises for a non-int transparency; the API has
				// no error return, so nil is the only possible answer.
				if got != nil {
					t.Fatalf("case %d transparent: want nil for the Python exception, got %x", i, got)
				}
			} else if c.Transparent.Value == nil {
				if got != nil {
					t.Fatalf("case %d transparent: want nil, got %x", i, got)
				}
			} else {
				want, err := base64.StdEncoding.DecodeString(*c.Transparent.Value)
				if err != nil {
					t.Fatalf("case %d: %v", i, err)
				}
				if got == nil {
					t.Fatalf("case %d transparent: want %x, got nil", i, want)
				}
				if !bytes.Equal(got, want) {
					t.Fatalf("case %d transparent: want %x, got %x", i, want, got)
				}
			}
		}
	}
	fmt.Printf("decoded formats: %v\n", decoded)
	fmt.Printf("ErrFormatNotPorted per format: %v\n", notPorted)
}

// safeOpen and safeRGBData turn an unexpected panic into a test failure that
// names the input, so a corpus mismatch is easy to reproduce.
var (
	caseIndex int
	caseData  string
)

func safeOpen(data []byte) (im *Image, err error) {
	defer func() {
		if r := recover(); r != nil {
			panic(fmt.Sprintf("case %d: Open panicked on %s: %v", caseIndex, shortData(data), r))
		}
	}()
	return Open(data)
}

func safeRGBData(im *Image) (mode string, data, alpha []byte, err error) {
	defer func() {
		if r := recover(); r != nil {
			panic(fmt.Sprintf("case %d: RGBData panicked for %s %s %dx%d: %v (data %s)", caseIndex, im.Format, im.Mode, im.Width, im.Height, r, caseData))
		}
	}()
	return im.RGBData()
}

func shortData(data []byte) string {
	if len(data) > 32 {
		return fmt.Sprintf("%x...", data[:32])
	}
	return fmt.Sprintf("%x", data)
}
