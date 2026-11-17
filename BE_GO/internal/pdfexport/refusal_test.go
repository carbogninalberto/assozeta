package pdfexport

import (
	"encoding/base64"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// Image data of a format pilimage only identifies (a TIFF, whose decoder and whose Pillow drawing are not ported yet)
// is refused instead of drawn differently from reportlab. Image data Pillow refuses is an exception of the export, and
// a logo Django's try around Image() catches builds as the '[LOGO]' paragraph; both are drawn as reportlab draws them.
func TestInputsNotPorted(t *testing.T) {
	header := func(logo, name string) *string {
		text := `<img src="` + logo + `" style="height: 1000px;"/><h1 style="font-size: 20px;">` + name + `</h1><h2>x</h2><h3>y</h3>`
		return &text
	}
	png := filepath.Join(t.TempDir(), "logo.png")
	if err := os.WriteFile(png, []byte("\x89PNG\r\n\x1a\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	base := DataTable{Columns: []string{"A"}, Rows: [][]Cell{{{Text: "a"}}}, Title: "T", Portrait: true, IsA3: true}
	tiff := base64.StdEncoding.EncodeToString([]byte("II*\x00\x08\x00\x00\x00\x0a\x00\x00\x01\x04\x00\x01\x00\x00\x00\x02\x00\x00\x00" +
		"\x01\x01\x04\x00\x01\x00\x00\x00\x02\x00\x00\x00\x02\x01\x03\x00\x03\x00\x00\x00\x86\x00\x00\x00" +
		"\x03\x01\x03\x00\x01\x00\x00\x00\x01\x00\x00\x00\x06\x01\x03\x00\x01\x00\x00\x00\x02\x00\x00\x00" +
		"\x11\x01\x04\x00\x01\x00\x00\x00\x8c\x00\x00\x00\x15\x01\x03\x00\x01\x00\x00\x00\x03\x00\x00\x00" +
		"\x16\x01\x04\x00\x01\x00\x00\x00\x02\x00\x00\x00\x17\x01\x04\x00\x01\x00\x00\x00\x0c\x00\x00\x00" +
		"\x1c\x01\x03\x00\x01\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\x08\x00\x08\x00\x08\x00" +
		"\x01\x02\x03\x01\x02\x03\x01\x02\x03\x01\x02\x03"))
	for name, input := range map[string]DataTable{
		"logo tiff":   {Header: header("data:image/jpeg;base64,"+tiff, "ASD")},
		"inline tiff": {Header: header("None", `A <img src="data:image/tiff;base64,`+tiff+`"/> B`)},
	} {
		input.Columns, input.Rows, input.Title, input.Portrait, input.IsA3 = base.Columns, base.Rows, base.Title, true, true
		if _, err := input.Build(time.Unix(946684800, 0)); !errors.Is(err, ErrNotPorted) {
			t.Errorf("%s: %v", name, err)
		}
	}
	// Image data Pillow refuses at the paragraph's ImageReader is the export's
	// exception (Pillow's text), and a logo Django's try around Image() catches
	// is the '[LOGO]' paragraph: both documents build as reportlab builds them.
	for name, input := range map[string]DataTable{
		"image file png": {Header: header("None", `A <img src="`+png+`"/> B`)},
		"image png":      {Header: header("None", `A <img src="data:image/png;base64,iVBORw0KGgo="/> B`)},
	} {
		input.Columns, input.Rows, input.Title, input.Portrait, input.IsA3 = base.Columns, base.Rows, base.Title, true, true
		if _, err := input.Build(time.Unix(946684800, 0)); !errors.Is(err, ErrLayout) || errors.Is(err, ErrNotPorted) {
			t.Errorf("%s: %v", name, err)
		}
	}
	for name, input := range map[string]DataTable{
		"png as jpeg": {Header: header("data:image/jpeg;base64,iVBORw0KGgo=", "ASD")},
		"cut jpeg":    {Header: header("data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/2wBD", "ASD")},
	} {
		input.Columns, input.Rows, input.Title, input.Portrait, input.IsA3 = base.Columns, base.Rows, base.Title, true, true
		if _, err := input.Build(time.Unix(946684800, 0)); err != nil {
			t.Errorf("%s: %v", name, err)
		}
	}
	// Markup reportlab's parser raises on, and an image source reportlab cannot open (ImageReader raises IOError), are
	// an exception of the export, not a refusal; the <index> and <onDraw> callbacks of its documents (they have no
	// canvas hook) fail the build, as here.
	for name, input := range map[string]DataTable{
		"image file missing": {Header: header("None", `A <img src="`+filepath.Join(t.TempDir(), "absent.jpg")+`"/> B`)},
		"image scheme":       {Header: header("None", `A <img src="gopher://127.0.0.1/logo.jpg"/> B`)},
		"unclosed tag":       {Header: header("None", "A <b>B")},
		"stray end tag":      {Header: header("None", "A </strike> B")},
		"index":              {Additional: []string{`saldi <index item="e"/> note`}},
		"onDraw":             {Header: header("None", `A <onDraw name="x"/> B`)},
		"greek non-latin1":   {Header: header("None", "A <greek>日</greek> B")},
	} {
		input.Columns, input.Rows, input.Title, input.Portrait, input.IsA3 = base.Columns, base.Rows, base.Title, true, true
		if _, err := input.Build(time.Unix(946684800, 0)); !errors.Is(err, ErrLayout) || errors.Is(err, ErrNotPorted) {
			t.Errorf("%s: %v", name, err)
		}
	}
	// A paragraph number that fp_str cannot format is an exception of the build (reportlab's OverflowError and
	// ValueError), and every paragraph the three documents build is laid out inside docTemplate.build.
	for name, input := range map[string]DataTable{
		"header number":     {Header: header("None", `A <font size="nan">B</font> C`)},
		"additional number": {Additional: []string{`<font size="nan">x</font>`}},
	} {
		input.Columns, input.Rows, input.Title, input.Portrait, input.IsA3 = base.Columns, base.Rows, base.Title, true, true
		if _, err := input.Build(time.Unix(946684800, 0)); !errors.Is(err, ErrLayout) || errors.Is(err, ErrNotPorted) {
			t.Errorf("%s: %v", name, err)
		}
	}
	if _, err := (ReportTable{Headers: []string{"A"}, Rows: [][]string{{`<font size="-inf">x</font>`}}, Title: "T"}).
		Build(time.Unix(946684800, 0)); !errors.Is(err, ErrLayout) || errors.Is(err, ErrNotPorted) {
		t.Errorf("report cell number: %v", err)
	}
	attendance := Attendance{Courses: []AttendanceCourse{{Course: "C", Dates: []string{"d"},
		Rows: []AttendanceRow{{Name: `<font size="nan">x</font>`, Attendance: []string{"P"}, Present: 1}}}}}
	if _, err := attendance.Build(time.Unix(946684800, 0)); !errors.Is(err, ErrLayout) || errors.Is(err, ErrNotPorted) {
		t.Errorf("attendance cell number: %v", err)
	}
	// Greek, unichar, the Courier and Times faces and plain markup are laid out as reportlab lays them out.
	plain := base
	plain.Header = header("None", "ASD > Aurora \"1990\" & <b>C</b>")
	if _, err := plain.Build(time.Unix(946684800, 0)); err != nil {
		t.Fatal(err)
	}
	// An anchor and the internal link that names it: the bookmark and the annotation of the page.
	anchored := base
	anchored.Header = header("None", `A <a name="top"/> <a href="#top">B</a> C`)
	if _, err := anchored.Build(time.Unix(946684800, 0)); err != nil {
		t.Fatal(err)
	}
	ported := base
	ported.Header = header("None", `<greek>abg</greek> <unichar code="0x263A"/> <unichar name="LATIN SMALL LETTER A"/> `+
		`<unichar name="EURO SIGN"/> <font name="Times-Italic">a</font> <font name="Courier">b</font>`)
	if _, err := ported.Build(time.Unix(946684800, 0)); err != nil {
		t.Fatal(err)
	}
	// Indents, justified lines, bullets, borders, autoLeading, endDots and the case transform of a paragraph are drawn
	// as reportlab draws them.
	placed := base
	placed.Header = header("None", `<para align="justify" lindent="12" rindent="6" findent="-4" borderwidth="1" bordercolor="red" `+
		`borderpadding="2" borderradius="3" autoleading="max" enddots="." texttransform="uppercase">`+
		`<bullet>*</bullet>un testo lungo che va a capo e si giustifica</para>`)
	if _, err := placed.Build(time.Unix(946684800, 0)); err != nil {
		t.Fatal(err)
	}
	transformed := base
	transformed.Header = header("None", `<para texttransform="capitalize">a <b>b</b> c</para>`)
	if _, err := transformed.Build(time.Unix(946684800, 0)); err != nil {
		t.Fatal(err)
	}
	// A sequence counts in a cell; the numbers of the process-wide Sequencer are the ones reportlab gives.
	sequenced := base
	sequenced.Rows = [][]Cell{{{Text: `saldi <seq id="golden"/> note`}}}
	if _, err := sequenced.Build(time.Unix(946684800, 0)); err != nil {
		t.Fatal(err)
	}
}

// Colour attributes are read for every string: the css forms, the colour
// names (plain, CMYK and transparent), Blacker/Whiter, the literals, the
// expressions and the HexColor values. None of them is an input the port
// refuses; the ones reportlab fails on are exceptions of the export.
func TestColoursAreNotRefused(t *testing.T) {
	header := func(logo, name string) *string {
		text := `<img src="` + logo + `" style="height: 1000px;"/><h1 style="font-size: 20px;">` + name + `</h1><h2>x</h2><h3>y</h3>`
		return &text
	}
	base := DataTable{Columns: []string{"A"}, Rows: [][]Cell{{{Text: "a"}}}, Title: "T", Portrait: true, IsA3: true}
	for name, markup := range map[string]string{
		"cmyk":           `<font color="cmyk(1,0,0,0)">a</font>`,
		"pcmyk alpha":    `<font color="pcmyka(100,0,0,0,50)">a</font>`,
		"rgba":           `<font color="rgba(255,0,0,0.5)">a</font>`,
		"hsl":            `<font color="hsla(0,100%,50%,0.5)">a</font>`,
		"transparent":    `<font color="transparent">a</font>`,
		"cmyk name":      `<font color="_CMYK_black">a</font>`,
		"reportlab name": `<font color="ReportLabBluePCMYK">a</font>`,
		"blacker":        `<font color="Blacker(red,0.5)">a</font>`,
		"whiter":         `<font color="Whiter(blue,0.25)">a</font>`,
		"tuple":          `<font color="(0.2,0.4,0.6)">a</font>`,
		"call":           `<font color="Color(0,1,0)">a</font>`,
		"string value":   `<font color="asNative(&quot;red&quot;)">a</font>`,
		"complex value":  `<font color="2j">a</font>`,
		"background":     `<font backcolor="rgb(1,2,3)">a</font>`,
		"underline":      `<u color="cmyk(1,0,0,0)">a</u>`,
		"strike":         `<strike color="hsl(0,100%,50%)">a</strike>`,
		"para":           `<para color="cmyk(0,1,0,0)" backcolor="rgba(0,0,0,0.5)" underlinecolor="hsl(0,100%,50%)" strikecolor="pcmyk(0,0,0,100)">a</para>`,
		"separation":     `<font color="CMYKColorSep(1,0,0,0,spotName='S')">a</font>`,
		"bad expression": `<font color="1+2j">a</font>`,
		"unknown name":   `<font color="rosso">a</font>`,
	} {
		input := base
		input.Header = header("None", "A "+markup+" B")
		if _, err := input.Build(time.Unix(946684800, 0)); errors.Is(err, ErrNotPorted) {
			t.Errorf("%s: refused: %v", name, err)
		}
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
