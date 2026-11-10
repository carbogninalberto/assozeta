package pdfexport

import (
	"strconv"
	"time"
	"unicode/utf8"
)

// reportlab.lib.units and pagesizes, computed in float64 as Python does.
var (
	cm     = 72.0 / 2.54
	mm     = cm * 0.1
	sizeA4 = [2]float64{210 * mm, 297 * mm}
	sizeA3 = [2]float64{297 * mm, 420 * mm}
	sizeA2 = [2]float64{420 * mm, 594 * mm}
)

// Cell is one value of a data table: Text is str(value), None marks a None
// (measured as 'None', drawn empty). Sequence marks a list or tuple, which
// reportlab takes for a list of flowables: it raises unless the value
// flattens to nothing (Empty), and then draws nothing.
type Cell struct {
	Text     string
	None     bool
	Sequence bool
	Empty    bool
}

// DataTable is the input of application.utils.printing.generate_pdf_from_data.
type DataTable struct {
	Columns []string
	Rows    [][]Cell
	Title   string
	// Align is the per-column alignment (LEFT, CENTER, RIGHT) or nil.
	Align []string
	// Header is the HTML header of the balance sheet, or nil.
	Header *string
	IsA3   bool
	// Portrait is landscape=False.
	Portrait bool
	// Additional are the additional_data texts, in the dict's order.
	Additional []string
}

// headerFooter is printing.header_footer: the title and the page number.
func headerFooter(title string, footer string) func(c *canvas, page int) {
	return func(c *canvas, page int) {
		c.saveState()
		x := c.pageW / 2.0
		c.setFontDefault("Helvetica-Bold", 9)
		c.drawCentredString(x, c.pageH-1*cm, title)
		c.setFontDefault("Helvetica", 9)
		text := "Pagina " + strconv.Itoa(page)
		if footer != "" {
			text = footer + " - " + text
		}
		c.drawCentredString(x, 1*cm, text)
		c.restoreState()
	}
}

// Build renders the table as generate_pdf_from_data does; now is the
// creation time. ErrLayout is an exception of the Django function.
func (d DataTable) Build(now time.Time) ([]byte, error) {
	if d.Align != nil {
		if len(d.Align) != len(d.Columns) {
			return nil, ErrLayout
		}
		for _, a := range d.Align {
			if a != "LEFT" && a != "CENTER" && a != "RIGHT" {
				return nil, ErrLayout
			}
		}
	}
	for _, row := range d.Rows {
		if len(row) != len(d.Columns) {
			return nil, ErrLayout
		}
	}
	if len(d.Columns) == 0 {
		return nil, ErrLayout // Table: data error
	}
	cells := make([][]tableCell, 0, len(d.Rows)+1)
	head := make([]tableCell, len(d.Columns))
	lengths := make([]int, len(d.Columns))
	for j, column := range d.Columns {
		head[j] = tableCell{text: column}
		lengths[j] = utf8.RuneCountInString(column)
	}
	cells = append(cells, head)
	for _, row := range d.Rows {
		line := make([]tableCell, len(row))
		for j, value := range row {
			n := 4
			switch {
			case value.None:
			case value.Sequence && !value.Empty:
				return nil, ErrLayout // 'str' object has no attribute 'wrapOn'
			case value.Sequence:
				line[j] = tableCell{list: true}
				n = utf8.RuneCountInString(value.Text)
			default:
				line[j] = tableCell{text: value.Text}
				n = utf8.RuneCountInString(value.Text)
			}
			if n > lengths[j] {
				lengths[j] = n
			}
		}
		cells = append(cells, line)
	}
	const scale = 3.5
	widths := make([]float64, len(lengths))
	total := 0.0
	for j, n := range lengths {
		w := float64(n) * (11.0 / 12.0) / scale
		if w < 1 {
			w = 1
		}
		widths[j] = w * cm
		total += widths[j]
	}
	t := newTable(cells, widths, 0)
	t.background("BACKGROUND", 0, 0, -1, 0, colorWhite)
	t.each(0, 0, -1, 0, func(s *cellStyle) { s.color = colorBlack })
	t.each(0, 0, -1, -1, func(s *cellStyle) { s.alignment = "CENTER" })
	t.each(0, 0, -1, 0, func(s *cellStyle) { s.fontName = "Helvetica-Bold" })
	t.each(0, 0, -1, 0, func(s *cellStyle) { s.bottomPadding = 6 })
	t.background("BACKGROUND", 0, 1, -1, -1, colorWhite)
	t.line("BOX", 0, 0, -1, -1, 1, colorBlack)
	t.line("GRID", 0, 0, -1, -1, 1, colorBlack)
	for j, a := range d.Align {
		t.each(j, 0, j, -1, func(s *cellStyle) { s.alignment = a })
	}
	selected := sizeA2
	for _, size := range [][2]float64{sizeA4, sizeA3, sizeA2} {
		available := size[1]
		if d.Portrait {
			available = size[0]
		}
		if total <= available-60 {
			selected = size
			break
		}
	}
	if d.IsA3 && selected == sizeA4 {
		selected = sizeA3
	}
	doc := &docTemplate{pageW: selected[1], pageH: selected[0], left: 30, right: 30, top: 30, bottom: 30, now: now,
		onPage: headerFooter(d.Title, "")}
	if d.Portrait {
		doc.pageW, doc.pageH = selected[0], selected[1]
	}
	elements := []flowable{t}
	if d.Header != nil {
		header, err := balanceHeader(*d.Header)
		if err != nil {
			return nil, err
		}
		elements = []flowable{header, t}
	}
	for _, text := range d.Additional {
		p, err := additionalParagraph(text)
		if err != nil {
			return nil, err
		}
		elements = append(elements, p)
	}
	return doc.build(elements)
}
