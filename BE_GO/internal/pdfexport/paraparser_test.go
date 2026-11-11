package pdfexport

import (
	"encoding/json"
	"errors"
	"os"
	"testing"
)

func rgbOf(c *color) any {
	if c == nil {
		return nil
	}
	return []float64{c.r, c.g, c.b}
}

func usValue(v usVal) any {
	if v.isNum {
		return v.num
	}
	return v.str
}

// describeFrag is the form generate_pdf_paragraphs.py writes for a fragment.
func describeFrag(f *frag) []any {
	lines := []any{}
	for _, l := range f.us {
		kind := "underline"
		if l.kind == lineStrike {
			kind = "strike"
		}
		var col *color
		if l.hasColor {
			col = &l.color
		}
		lines = append(lines, []any{l.n, kind, rgbOf(col), usValue(l.width), usValue(l.offset), l.rise, l.mult, usValue(l.gap)})
	}
	links := []any{}
	for _, l := range f.links {
		links = append(links, []any{l.n, l.target})
	}
	var img any
	if f.img != nil {
		dimOf := func(d dim) any {
			if d.pct {
				return []any{"pct", d.v}
			}
			return d.v
		}
		va := f.img.valign
		var vaValue any = va.name
		switch {
		case va.pct:
			vaValue = []any{"pct", va.v}
		case va.name == "":
			vaValue = va.v
		}
		img = []any{"img", f.img.src, dimOf(f.img.width), dimOf(f.img.height), vaValue}
	}
	return []any{f.font.name, f.text, f.lineBreak, f.size, rgbOf(&f.color), f.rise, lines, links, rgbOf(f.back), f.nobr, img}
}

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
			Text      string `json:"text"`
			Font      string `json:"font"`
			Refusable bool   `json:"refusable"`
			Error     bool   `json:"error"`
			Other     bool   `json:"other"`
			Frags     []any  `json:"frags"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &file); err != nil {
		t.Fatal(err)
	}
	exact, failures := 0, 0
	for _, item := range file.Cases {
		frags, _, err := parseParagraph(paraClean(item.Text), paraStyle{fontName: item.Font, fontSize: 9, leading: 12})
		if errors.Is(err, ErrNotPorted) {
			if !item.Refusable && !item.Other {
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
			got := []any{}
			for _, f := range frags {
				got = append(got, describeFrag(f))
			}
			want, _ := json.Marshal(item.Frags)
			have, _ := json.Marshal(got)
			if len(item.Frags) == 0 && len(got) == 0 {
				want, have = nil, nil
			}
			if string(want) != string(have) {
				t.Errorf("%q:\nwant %s\n got %s", item.Text, want, have)
			}
			exact++
		}
	}
	if exact < 500 || failures < 500 {
		t.Fatalf("%d parsed and %d failing texts compared", exact, failures)
	}
}
