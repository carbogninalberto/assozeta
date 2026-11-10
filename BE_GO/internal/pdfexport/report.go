package pdfexport

import (
	"math"
	"strconv"
	"time"
)

// verticalText is the attendance export's VerticalText flowable: the text
// drawn bottom to top in Helvetica-Bold 7.
type verticalText struct {
	text          string
	width, height float64
}

func newVerticalText(text string) *verticalText {
	return &verticalText{text: text, width: 7 + 2, height: stringWidth(text, fontsByName["Helvetica-Bold"], 7) + 4}
}

func (v *verticalText) wrap(*canvas, float64, float64) (float64, float64) { return v.width, v.height }
func (v *verticalText) split(*canvas, float64, float64) []flowable        { return nil }
func (v *verticalText) spaceBefore() float64                              { return 0 }
func (v *verticalText) spaceAfter() float64                               { return 0 }
func (v *verticalText) hAlign() string                                    { return "LEFT" }

func (v *verticalText) draw(c *canvas) {
	c.saveState()
	cos, sin := math.Cos(90*math.Pi/180), math.Sin(90*math.Pi/180)
	c.transform(cos, sin, -sin, cos, 0, 0)
	c.setFontDefault("Helvetica-Bold", 7)
	c.drawString(2, -7/3.0, v.text)
	c.restoreState()
}

func paragraphCell(text string, style paraStyle) (tableCell, error) {
	p, err := newParagraph(text, style)
	if err != nil {
		return tableCell{}, err
	}
	return tableCell{list: true, flows: []flowable{p}}, nil
}

// reportStyle is the TableStyle both saved-report documents share.
func reportStyle(t *table, headerSize float64) {
	t.background("BACKGROUND", 0, 0, -1, 0, hexColor(0xf0f0f0))
	t.each(0, 0, -1, 0, func(s *cellStyle) { s.color = colorBlack })
	t.each(0, 0, -1, -1, func(s *cellStyle) { s.alignment = "CENTER" })
	t.each(0, 0, -1, 0, func(s *cellStyle) { s.fontName = "Helvetica-Bold" })
	t.each(0, 0, -1, 0, func(s *cellStyle) { s.fontSize = headerSize })
	if t.nrows() > 1 {
		t.each(0, 1, -1, -1, func(s *cellStyle) { s.fontSize = 7 })
	}
	t.each(0, 0, -1, 0, func(s *cellStyle) { s.bottomPadding = 6 })
}

// ReportTable is the saved-report PDFExporter from its headers and its rows
// of str() values on: a Paragraph in every cell, the column widths left to
// reportlab, the header row repeated on every page.
type ReportTable struct {
	Headers []string
	Rows    [][]string
	// Title is header_text or title; Footer is footer_text.
	Title, Footer         string
	TextBefore, TextAfter string
}

func pageFor(columns, small, medium int) [2]float64 {
	switch {
	case columns <= small:
		return sizeA4
	case columns <= medium:
		return sizeA3
	}
	return sizeA2
}

// Build renders the document; now is the creation time.
func (r ReportTable) Build(now time.Time) ([]byte, error) {
	if len(r.Headers) == 0 {
		return nil, ErrLayout
	}
	header := paraStyle{fontName: "Helvetica-Bold", fontSize: 8, leading: 12}
	cell := paraStyle{fontName: "Helvetica", fontSize: 7, leading: 12}
	cells := make([][]tableCell, 0, len(r.Rows)+1)
	line := make([]tableCell, len(r.Headers))
	for j, text := range r.Headers {
		var err error
		if line[j], err = paragraphCell(text, header); err != nil {
			return nil, err
		}
	}
	cells = append(cells, line)
	for _, row := range r.Rows {
		if len(row) != len(r.Headers) {
			return nil, ErrLayout
		}
		line := make([]tableCell, len(row))
		for j, text := range row {
			var err error
			if line[j], err = paragraphCell(text, cell); err != nil {
				return nil, err
			}
		}
		cells = append(cells, line)
	}
	t := newTable(cells, nil, 1)
	reportStyle(t, 8)
	if t.nrows() > 1 {
		t.background("BACKGROUND", 0, 1, -1, -1, colorWhite)
	}
	t.line("BOX", 0, 0, -1, -1, 1, colorBlack)
	t.line("GRID", 0, 0, -1, -1, 0.5, colorGrey)
	if t.nrows() > 1 {
		t.background("ROWBACKGROUNDS", 0, 1, -1, -1, colorWhite, hexColor(0xfafafa))
	}
	size := pageFor(len(r.Headers), 5, 10)
	doc := &docTemplate{pageW: size[1], pageH: size[0], left: 30, right: 30, top: 40, bottom: 30, now: now,
		onPage: headerFooter(r.Title, r.Footer)}
	body := paraStyle{fontName: "Helvetica", fontSize: 9, leading: 12}
	var flowables []flowable
	if r.TextBefore != "" {
		p, err := newParagraph(r.TextBefore, body)
		if err != nil {
			return nil, err
		}
		flowables = append(flowables, p, spacer{1, 0.5 * cm})
	}
	flowables = append(flowables, t)
	if r.TextAfter != "" {
		p, err := newParagraph(r.TextAfter, body)
		if err != nil {
			return nil, err
		}
		flowables = append(flowables, spacer{1, 0.5 * cm}, p)
	}
	return doc.build(flowables)
}

// AttendanceRow is one enrolled person of an attendance matrix.
type AttendanceRow struct {
	Name       string
	Attendance []string
	Present    int
}

// AttendanceCourse is one course matrix: its dates and its rows.
type AttendanceCourse struct {
	Course string
	Dates  []string
	Rows   []AttendanceRow
}

// Attendance is export_attendance_matrix_pdf: one table per course with the
// dates written bottom to top, a title and a page break between courses when
// there are several.
type Attendance struct{ Courses []AttendanceCourse }

func (a Attendance) courseTable(course AttendanceCourse) (*table, error) {
	header := paraStyle{fontName: "Helvetica-Bold", fontSize: 7, leading: 12, centred: true}
	centre := paraStyle{fontName: "Helvetica", fontSize: 7, leading: 12, centred: true}
	name := paraStyle{fontName: "Helvetica", fontSize: 7, leading: 12}
	add := func(line []tableCell, text string, style paraStyle) ([]tableCell, error) {
		cell, err := paragraphCell(text, style)
		return append(line, cell), err
	}
	head, err := add(nil, "#", header)
	if err != nil {
		return nil, err
	}
	if head, err = add(head, "Nome e Cognome", header); err != nil {
		return nil, err
	}
	for _, date := range course.Dates {
		head = append(head, tableCell{list: true, flows: []flowable{newVerticalText(date)}})
	}
	if head, err = add(head, "Presenze", header); err != nil {
		return nil, err
	}
	cells := [][]tableCell{head}
	for i, row := range course.Rows {
		line, err := add(nil, strconv.Itoa(i+1), centre)
		if err != nil {
			return nil, err
		}
		if line, err = add(line, row.Name, name); err != nil {
			return nil, err
		}
		for _, mark := range row.Attendance {
			if line, err = add(line, mark, centre); err != nil {
				return nil, err
			}
		}
		if line, err = add(line, strconv.Itoa(row.Present), centre); err != nil {
			return nil, err
		}
		if len(line) != len(head) {
			return nil, ErrLayout
		}
		cells = append(cells, line)
	}
	widths := []float64{1.0 * cm, 5.5 * cm}
	for range course.Dates {
		widths = append(widths, 1.0*cm)
	}
	widths = append(widths, 1.8*cm)
	t := newTable(cells, widths, 1)
	reportStyle(t, 7)
	if t.nrows() > 1 {
		t.each(1, 1, 1, -1, func(s *cellStyle) { s.alignment = "LEFT" })
	}
	t.each(0, 0, -1, 0, func(s *cellStyle) { s.topPadding = 14 })
	t.line("BOX", 0, 0, -1, -1, 1, colorBlack)
	t.line("GRID", 0, 0, -1, -1, 0.5, colorGrey)
	if t.nrows() > 1 {
		t.background("ROWBACKGROUNDS", 0, 1, -1, -1, colorWhite, hexColor(0xfafafa))
	}
	t.each(0, 0, -1, -1, func(s *cellStyle) { s.valign = "MIDDLE" })
	return t, nil
}

// Build renders the document; now is the creation time.
func (a Attendance) Build(now time.Time) ([]byte, error) {
	if len(a.Courses) == 0 {
		return nil, ErrLayout
	}
	columns := 0
	for _, course := range a.Courses {
		if n := len(course.Dates) + 3; n > columns {
			columns = n
		}
	}
	size := pageFor(columns, 15, 25)
	doc := &docTemplate{pageW: size[1], pageH: size[0], left: 30, right: 30, top: 40, bottom: 30, now: now,
		onPage: headerFooter("Presenze", "")}
	title := paraStyle{fontName: "Helvetica-Bold", fontSize: 12, leading: 18, spaceBefore: 12, spaceAfter: 8}
	var flowables []flowable
	for i, course := range a.Courses {
		if len(a.Courses) > 1 {
			p, err := newParagraph(course.Course, title)
			if err != nil {
				return nil, err
			}
			flowables = append(flowables, p, spacer{1, 0.3 * cm})
		}
		t, err := a.courseTable(course)
		if err != nil {
			return nil, err
		}
		flowables = append(flowables, t)
		if i < len(a.Courses)-1 {
			flowables = append(flowables, pageBreak{})
		}
	}
	return doc.build(flowables)
}
