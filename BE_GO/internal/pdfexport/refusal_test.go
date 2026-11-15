package pdfexport

import (
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// Paragraph tags whose drawing is not ported (lines, links, others), paragraph attributes, and logo data that is not a
// JPEG this package reads, are refused instead of drawn differently from reportlab.
func TestInputsNotPorted(t *testing.T) {
	header := func(logo, name string) *string {
		text := `<img src="` + logo + `" style="height: 1000px;"/><h1 style="font-size: 20px;">` + name + `</h1><h2>x</h2><h3>y</h3>`
		return &text
	}
	base := DataTable{Columns: []string{"A"}, Rows: [][]Cell{{{Text: "a"}}}, Title: "T", Portrait: true, IsA3: true}
	for name, input := range map[string]DataTable{
		"image png":      {Header: header("None", `A <img src="data:image/png;base64,iVBORw0KGgo="/> B`)},
		"anchor":         {Header: header("None", `A <a name="top"/> B`)},
		"internal link":  {Header: header("None", `A <a href="#top">B</a> C`)},
		"bullet":         {Header: header("None", "A <bullet>b</bullet> B")},
		"para attribute": {Header: header("None", `<para align="justify" lindent="12">A</para>`)},
		"text transform": {Header: header("None", `<para texttransform="uppercase">a</para>`)},
		"unichar name":   {Header: header("None", `A <unichar name="LATIN SMALL LETTER A"/> B`)},
		"png as jpeg":    {Header: header("data:image/jpeg;base64,iVBORw0KGgo=", "ASD")},
		"cut jpeg":       {Header: header("data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBD", "ASD")},
	} {
		input.Columns, input.Rows, input.Title, input.Portrait, input.IsA3 = base.Columns, base.Rows, base.Title, true, true
		if _, err := input.Build(time.Unix(946684800, 0)); !errors.Is(err, ErrNotPorted) {
			t.Errorf("%s: %v", name, err)
		}
	}
	// An image file or URL and the <seq> counter depend on something outside the
	// request: application state (the 500 page), never a handoff.
	for name, markup := range map[string]string{"image file": `A <img src="/tmp/logo.jpg"/> B`, "sequence": "A <seq/>B"} {
		input := base
		input.Header = header("None", markup)
		if _, err := input.Build(time.Unix(946684800, 0)); !errors.Is(err, ErrOutsideInput) || !errors.Is(err, refusal.ErrAppState) ||
			errors.Is(err, refusal.ErrNotPorted) {
			t.Errorf("%s: %v", name, err)
		}
	}
	// Markup reportlab's parser raises on is an exception of the export, not a refusal; the <index> and <onDraw>
	// callbacks of its documents (they have no canvas hook) fail the build, as here.
	for name, input := range map[string]DataTable{
		"unclosed tag":     {Header: header("None", "A <b>B")},
		"stray end tag":    {Header: header("None", "A </strike> B")},
		"index":            {Additional: []string{`saldi <index item="e"/> note`}},
		"onDraw":           {Header: header("None", `A <onDraw name="x"/> B`)},
		"greek non-latin1": {Header: header("None", "A <greek>日</greek> B")},
	} {
		input.Columns, input.Rows, input.Title, input.Portrait, input.IsA3 = base.Columns, base.Rows, base.Title, true, true
		if _, err := input.Build(time.Unix(946684800, 0)); !errors.Is(err, ErrLayout) || errors.Is(err, ErrNotPorted) {
			t.Errorf("%s: %v", name, err)
		}
	}
	// Greek, unichar, the Courier and Times faces and plain markup are laid out as reportlab lays them out.
	plain := base
	plain.Header = header("None", "ASD > Aurora \"1990\" & <b>C</b>")
	if _, err := plain.Build(time.Unix(946684800, 0)); err != nil {
		t.Fatal(err)
	}
	ported := base
	ported.Header = header("None", `<greek>abg</greek> <unichar code="0x263A"/> <font name="Times-Italic">a</font> <font name="Courier">b</font>`)
	if _, err := ported.Build(time.Unix(946684800, 0)); err != nil {
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
