package pdfexport

import (
	"encoding/json"
	"os"
	"strconv"
	"strings"
	"testing"
)

// The case mappings come from CPython 3.13 (TESTS/fixtures/generate_pdf_paragraphs.py):
// str.upper, str.lower and str.title of every code point, and strings that
// mix the scripts whose casing is contextual (the final sigma, the dotted
// capital I, the title case digraphs).
func TestCaseMappings(t *testing.T) {
	raw, err := os.ReadFile("testdata/casemapping.json")
	if err != nil {
		t.Fatal(err)
	}
	var file struct {
		Mappings map[string]string `json:"mappings"`
		Corpus   [][4]string       `json:"corpus"`
	}
	if err := json.Unmarshal(raw, &file); err != nil {
		t.Fatal(err)
	}
	if len(file.Mappings) < 2000 || len(file.Corpus) < 200 {
		t.Fatalf("%d mappings and %d strings", len(file.Mappings), len(file.Corpus))
	}
	mapped := make(map[rune][3]string, len(file.Mappings))
	for key, value := range file.Mappings {
		code, err := strconv.Atoi(key)
		if err != nil {
			t.Fatal(err)
		}
		parts := strings.Split(value, "|")
		if len(parts) != 3 {
			t.Fatalf("%s: %q", key, value)
		}
		mapped[rune(code)] = [3]string{parts[0], parts[1], parts[2]}
	}
	for code := rune(0); code <= 0x10FFFF; code++ {
		if code >= 0xD800 && code <= 0xDFFF {
			continue // Go converts a lone surrogate to U+FFFD
		}
		text := string(code)
		want, ok := mapped[code]
		if !ok {
			want = [3]string{text, text, text}
		}
		got := [3]string{pythonUpper(text), pythonLower(text), pythonTitle(text)}
		if got != want {
			t.Fatalf("U+%04X: upper %q, lower %q, title %q; want %q, %q, %q", code, got[0], got[1], got[2],
				want[0], want[1], want[2])
		}
	}
	for _, item := range file.Corpus {
		got := [3]string{pythonUpper(item[0]), pythonLower(item[0]), pythonTitle(item[0])}
		want := [3]string{item[1], item[2], item[3]}
		if got != want {
			t.Fatalf("%q: upper %q, lower %q, title %q; want %q, %q, %q", item[0], got[0], got[1], got[2],
				want[0], want[1], want[2])
		}
	}
}
