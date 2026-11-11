package pdfexport

import (
	"errors"
	"testing"
	"time"
)

// Paragraph tags whose drawing is not ported (lines, links, other sizes and
// colours, images), paragraph attributes, and logo data that is not a JPEG
// this package reads, are refused instead of drawn differently from
// reportlab.
func TestInputsNotPorted(t *testing.T) {
	header := func(logo, name string) *string {
		text := `<img src="` + logo + `" style="height: 1000px;"/><h1 style="font-size: 20px;">` + name + `</h1><h2>x</h2><h3>y</h3>`
		return &text
	}
	base := DataTable{Columns: []string{"A"}, Rows: [][]Cell{{{Text: "a"}}}, Title: "T", Portrait: true, IsA3: true}
	for name, input := range map[string]DataTable{
		"anchor":         {Header: header("None", `A <a name="top"/> B`)},
		"internal link":  {Header: header("None", `A <a href="#top">B</a> C`)},
		"greek":          {Header: header("None", "A <greek>B</greek>")},
		"para attribute": {Header: header("None", `<para align="center">A</para>`)},
		"additional":     {Additional: []string{"saldi <index item=\"e\"/> note"}},
		"png as jpeg":    {Header: header("data:image/jpeg;base64,iVBORw0KGgo=", "ASD")},
		"cut jpeg":       {Header: header("data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBD", "ASD")},
	} {
		input.Columns, input.Rows, input.Title, input.Portrait, input.IsA3 = base.Columns, base.Rows, base.Title, true, true
		if _, err := input.Build(time.Unix(946684800, 0)); !errors.Is(err, ErrNotPorted) {
			t.Errorf("%s: %v", name, err)
		}
	}
	// Markup reportlab's parser raises on is an exception of the export, not a refusal.
	broken := base
	broken.Header = header("None", "A <b>B")
	if _, err := broken.Build(time.Unix(946684800, 0)); !errors.Is(err, ErrLayout) || errors.Is(err, ErrNotPorted) {
		t.Errorf("unclosed tag: %v", err)
	}
	stray := base
	stray.Header = header("None", "A </strike> B")
	if _, err := stray.Build(time.Unix(946684800, 0)); !errors.Is(err, ErrLayout) || errors.Is(err, ErrNotPorted) {
		t.Errorf("stray end tag: %v", err)
	}
	plain := base
	plain.Header = header("None", "ASD > Aurora \"1990\" & <b>C</b>")
	if _, err := plain.Build(time.Unix(946684800, 0)); err != nil {
		t.Fatal(err)
	}
}

// PDFTextObject.moveCursor replaces a move that directly follows another.
func TestMoveCursorMerges(t *testing.T) {
	tx := &textObject{}
	tx.setXPos(10)
	tx.setXPos(-10)
	tx.setXPos(2.5)
	if len(tx.code) != 1 || tx.code[0] != "2.5 0 Td" {
		t.Fatalf("%q", tx.code)
	}
}
