package pdfexport

import (
	"errors"
	"testing"
	"time"
)

// Paragraph text with markup, entities or soft hyphens, and a logo that is
// not a plain JPEG, are refused instead of drawn differently from reportlab.
func TestInputsNotPorted(t *testing.T) {
	header := func(logo, name string) *string {
		text := `<img src="` + logo + `" style="height: 1000px;"/><h1 style="font-size: 20px;">` + name + `</h1><h2>x</h2><h3>y</h3>`
		return &text
	}
	base := DataTable{Columns: []string{"A"}, Rows: [][]Cell{{{Text: "a"}}}, Title: "T", Portrait: true, IsA3: true}
	for name, input := range map[string]DataTable{
		"ampersand":   {Header: header("None", "Sport & Fun")},
		"entity":      {Header: header("None", "Sport &amp; Fun")},
		"markup":      {Header: header("None", "A <b>B</b>")},
		"less than":   {Header: header("None", "A < B")},
		"soft hyphen": {Header: header("None", "Poli­sportiva")},
		"additional":  {Additional: []string{"saldi & note"}},
		"png as jpeg": {Header: header("data:image/jpeg;base64,iVBORw0KGgo=", "ASD")},
		"bad base64":  {Header: header("data:image/jpeg;base64,@@@", "ASD")},
	} {
		input.Columns, input.Rows, input.Title, input.Portrait, input.IsA3 = base.Columns, base.Rows, base.Title, true, true
		if _, err := input.Build(time.Unix(946684800, 0)); !errors.Is(err, ErrNotPorted) {
			t.Errorf("%s: %v", name, err)
		}
	}
	plain := base
	plain.Header = header("None", "ASD > Aurora \"1990\"")
	if _, err := plain.Build(time.Unix(946684800, 0)); err != nil {
		t.Fatal(err)
	}
}
