package pdfexport

import (
	"encoding/json"
	"errors"
	"os"
	"testing"
)

// The corpus comes from reportlab's own Paragraph
// (TESTS/fixtures/generate_pdf_paragraphs.py): the fragments of a text, an
// exception, or fragments this package refuses.
func TestParagraphParserCorpus(t *testing.T) {
	raw, err := os.ReadFile("testdata/paragraphs.json")
	if err != nil {
		t.Fatal(err)
	}
	var file struct {
		Cases []struct {
			Text      string  `json:"text"`
			Font      string  `json:"font"`
			Refusable bool    `json:"refusable"`
			Error     bool    `json:"error"`
			Other     bool    `json:"other"`
			Frags     [][]any `json:"frags"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &file); err != nil {
		t.Fatal(err)
	}
	exact, failures := 0, 0
	for _, item := range file.Cases {
		frags, err := parseParagraph(paraClean(item.Text), paraStyle{fontName: item.Font, fontSize: 9, leading: 12})
		if errors.Is(err, ErrNotPorted) {
			if !item.Refusable {
				t.Errorf("%q: refused", item.Text)
			}
			continue
		}
		switch {
		case item.Other:
			t.Errorf("%q: reportlab makes fragments this package does not draw; got %v, %v", item.Text, len(frags), err)
		case item.Error:
			if !errors.Is(err, ErrLayout) {
				t.Errorf("%q: reportlab raises; got %d fragments, %v", item.Text, len(frags), err)
			}
			failures++
		default:
			if err != nil {
				t.Errorf("%q: %v", item.Text, err)
				continue
			}
			same := len(frags) == len(item.Frags)
			for i := 0; same && i < len(frags); i++ {
				same = frags[i].font.name == item.Frags[i][0].(string) && frags[i].text == item.Frags[i][1].(string) &&
					frags[i].lineBreak == item.Frags[i][2].(bool)
			}
			if !same {
				got := [][]any{}
				for _, f := range frags {
					got = append(got, []any{f.font.name, f.text, f.lineBreak})
				}
				t.Errorf("%q:\nwant %v\n got %v", item.Text, item.Frags, got)
			}
			exact++
		}
	}
	if exact < 500 || failures < 500 {
		t.Fatalf("%d parsed and %d failing texts compared", exact, failures)
	}
}
