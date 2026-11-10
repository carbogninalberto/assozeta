package pdfexport

import "strings"

// flowable is the part of platypus.Flowable the documents use.
type flowable interface {
	// wrap returns the size used within the available space.
	wrap(c *canvas, availWidth, availHeight float64) (float64, float64)
	// split returns the pieces that fit the available space, or nil.
	split(c *canvas, availWidth, availHeight float64) []flowable
	// draw draws at the origin (drawOn has translated the canvas).
	draw(c *canvas)
	spaceBefore() float64
	spaceAfter() float64
	// hAlign is "" for a flowable without the attribute.
	hAlign() string
}

type cellStyle struct {
	fontName      string
	fontSize      float64
	leading       float64
	leftPadding   float64
	rightPadding  float64
	topPadding    float64
	bottomPadding float64
	color         color
	alignment     string
	valign        string
}

func defaultCellStyle() *cellStyle {
	return &cellStyle{fontName: "Helvetica", fontSize: 10, leading: 12, leftPadding: 6, rightPadding: 6, topPadding: 3,
		bottomPadding: 3, color: color{named: true}, alignment: "LEFT", valign: "BOTTOM"}
}

// tableCell is a str cell (text, None normalized to ”) or a list of flowables.
type tableCell struct {
	text  string
	flows []flowable
	list  bool
}

type lineCommand struct {
	op             string
	sc, sr, ec, er int
	weight         float64
	color          color
}

type backgroundCommand struct {
	op             string // BACKGROUND or ROWBACKGROUNDS
	sc, sr, ec, er int
	colors         []color
}

// table is platypus.Table without spans, special rows or in-row splits.
type table struct {
	cells      [][]tableCell
	styles     [][]*cellStyle
	argW       []float64 // NaN-free: every width is given, or autoW is set
	autoW      bool
	argH       []float64 // nil: computed
	repeatRows int
	lines      []lineCommand
	backs      []backgroundCommand
	align      string
	after      float64

	colWidths    []float64
	rowHeights   []float64
	colPositions []float64
	rowPositions []float64
	width        float64
	height       float64
	widthOnce    bool
}

func newTable(cells [][]tableCell, colWidths []float64, repeatRows int) *table {
	t := &table{cells: cells, argW: colWidths, autoW: colWidths == nil, repeatRows: repeatRows, align: "CENTER"}
	t.styles = make([][]*cellStyle, len(cells))
	for i, row := range cells {
		t.styles[i] = make([]*cellStyle, len(row))
		for j := range row {
			t.styles[i][j] = defaultCellStyle()
		}
	}
	return t
}

func (t *table) nrows() int { return len(t.cells) }
func (t *table) ncols() int { return len(t.cells[0]) }

func (t *table) normRange(sc, sr, ec, er int) (int, int, int, int) {
	if sc < 0 {
		sc += t.ncols()
	}
	if ec < 0 {
		ec += t.ncols()
	}
	if sr < 0 {
		sr += t.nrows()
	}
	if er < 0 {
		er += t.nrows()
	}
	return sc, sr, ec, er
}

// each applies a cell style command over its range.
func (t *table) each(sc, sr, ec, er int, apply func(*cellStyle)) {
	sc, sr, ec, er = t.normRange(sc, sr, ec, er)
	for i := sr; i <= er; i++ {
		for j := sc; j <= ec; j++ {
			apply(t.styles[i][j])
		}
	}
}

func (t *table) line(op string, sc, sr, ec, er int, weight float64, col color) {
	t.lines = append(t.lines, lineCommand{op, sc, sr, ec, er, weight, col})
}

func (t *table) background(op string, sc, sr, ec, er int, colors ...color) {
	t.backs = append(t.backs, backgroundCommand{op, sc, sr, ec, er, colors})
}

func (t *table) spaceBefore() float64 { return 0 }
func (t *table) spaceAfter() float64  { return t.after }
func (t *table) hAlign() string       { return t.align }

func cellLines(cell tableCell) []string { return strings.Split(cell.text, "\n") }

// listCellGeom is Table._listCellGeom for a list of flowables.
func listCellGeom(c *canvas, flows []flowable, w float64, s *cellStyle, aH float64, widths, heights *[]float64) (float64, float64) {
	if len(flows) == 0 {
		return 0, 0
	}
	aW := w - s.leftPadding - s.rightPadding
	aH = aH - s.topPadding - s.bottomPadding
	t, width := 0.0, 0.0
	sb0, first, sa := 0.0, true, 0.0
	for _, v := range flows {
		vw, vh := v.wrap(c, aW, aH)
		sb := v.spaceBefore()
		sa = v.spaceAfter()
		if widths != nil {
			*widths = append(*widths, vw)
		}
		if heights != nil {
			*heights = append(*heights, vh)
		}
		if vw > width {
			width = vw
		}
		t += vh + sa + sb
		if first {
			sb0, first = sb, false
		}
	}
	return width, t - sb0 - sa
}

func (t *table) calcHeights(c *canvas, widths []float64) {
	if t.argH != nil {
		t.rowHeights = t.argH
	} else {
		heights := make([]float64, t.nrows())
		for i, row := range t.cells {
			h := 0.0
			for j, cell := range row {
				s := t.styles[i][j]
				var v float64
				if cell.list {
					saved := c.state
					c.state = canvasState{fontName: s.fontName, fontSize: s.fontSize, leading: s.leading}
					_, v = listCellGeom(c, cell.flows, widths[j], s, 72000, nil, nil)
					c.state = saved
				} else {
					v = s.leading * float64(len(cellLines(cell)))
				}
				v += s.bottomPadding + s.topPadding
				if v > h {
					h = v
				}
			}
			heights[i] = h
		}
		t.rowHeights = heights
	}
	// Row positions from the bottom, with the compensated sum reportlab uses.
	positions := make([]float64, 0, t.nrows()+1)
	height, comp := 0.0, 0.0
	for i := t.nrows() - 1; i >= 0; i-- {
		positions = append(positions, height)
		y := t.rowHeights[i] - comp
		sum := height + y
		comp = (sum - height) - y
		height = sum
	}
	positions = append(positions, height)
	t.height = height
	for i, j := 0, len(positions)-1; i < j; i, j = i+1, j-1 {
		positions[i], positions[j] = positions[j], positions[i]
	}
	t.rowPositions = positions
}

func (t *table) calcWidths(widths []float64) {
	if t.widthOnce {
		return
	}
	t.colWidths = widths
	t.colPositions = []float64{0}
	width := 0.0
	for _, w := range widths {
		width += w
		t.colPositions = append(t.colPositions, width)
	}
	t.width = width
	t.widthOnce = true
}

func (t *table) calc(c *canvas, availWidth float64) error {
	widths := t.argW
	if t.autoW {
		if t.widthOnce {
			widths = t.colWidths
		} else {
			var err error
			if widths, err = t.preliminaryWidths(c, availWidth); err != nil {
				return err
			}
		}
	}
	t.calcHeights(c, widths)
	t.calcWidths(widths)
	return nil
}

func (t *table) wrap(c *canvas, availWidth, availHeight float64) (float64, float64) {
	_ = t.calc(c, availWidth)
	return t.width, t.height
}

// splitLines is Table._splitLineCmds for a split between rows at n.
func (t *table) splitLines(n int) []lineCommand {
	var out []lineCommand
	for _, cmd := range t.lines {
		sc, sr, ec, er := t.normRange(cmd.sc, cmd.sr, cmd.ec, cmd.er)
		with := func(op string, sc, sr, ec, er int) lineCommand {
			return lineCommand{op, sc, sr, ec, er, cmd.weight, cmd.color}
		}
		switch cmd.op {
		case "BOX", "GRID":
			if sr < n && er >= n {
				out = append(out, with("LINEABOVE", sc, sr, ec, sr), with("LINEBEFORE", sc, sr, sc, er),
					with("LINEAFTER", ec, sr, ec, er), with("LINEBELOW", sc, er, ec, er))
				if cmd.op == "GRID" {
					out = append(out, with("LINEBELOW", sc, n-1, ec, n-1), with("LINEABOVE", sc, n, ec, n),
						with("INNERGRID", sc, sr, ec, er))
				}
			} else {
				out = append(out, with(cmd.op, sc, sr, ec, er))
			}
		case "INNERGRID":
			if sr < n && er >= n {
				out = append(out, with("LINEBELOW", sc, n-1, ec, n-1), with("LINEABOVE", sc, n, ec, n))
			}
			out = append(out, with(cmd.op, sc, sr, ec, er))
		case "LINEBELOW":
			if sr < n && er >= n-1 {
				out = append(out, with("LINEABOVE", sc, n, ec, n))
			}
			out = append(out, with(cmd.op, sc, sr, ec, er))
		case "LINEABOVE":
			if sr <= n && er >= n {
				out = append(out, with("LINEBELOW", sc, n-1, ec, n-1))
			}
			out = append(out, with(cmd.op, sc, sr, ec, er))
		default:
			out = append(out, with(cmd.op, sc, sr, ec, er))
		}
	}
	return out
}

// rangeFirst is Table._cr_0: a command's rows within the first part.
func rangeFirst(n, nrows, sr, er int) (int, int, bool) {
	if sr < 0 {
		sr += nrows
	}
	if sr >= n {
		return 0, 0, false
	}
	if er >= n {
		er = n - 1
	}
	return sr, er, true
}

// rangeRest is Table._cr_1_0: a command's rows within the second part.
func rangeRest(n, sr, er int) (int, int, bool) {
	if er >= 0 && er < n {
		return 0, 0, false
	}
	if sr >= 0 && sr < n {
		sr = 0
	}
	if sr >= n {
		sr -= n
	}
	if er >= n {
		er -= n
	}
	return sr, er, true
}

// rangeRepeat is Table._cr_1_1: the ranges of a command in a second part
// that starts with the repeated rows.
func rangeRepeat(n, nrows, repeat, sr, er int) [][2]int {
	if sr < 0 {
		sr += nrows
	}
	if er < 0 {
		er += nrows
	}
	var out [][2]int
	low, high := -1, -1
	for row := sr; row <= er; row++ {
		if row < repeat {
			if low < 0 {
				low = row
			}
			high = row
		}
	}
	if low >= 0 {
		out = append(out, [2]int{low, high})
	}
	if er < n {
		return out
	}
	first, last := sr-n, er-n
	if first < 0 {
		first = 0
	}
	if last < 0 {
		last = 0
	}
	return append(out, [2]int{first + repeat, last + repeat})
}

// splitRows is Table._splitRows between rows (the tables neither span nor
// split within a row).
func (t *table) splitRows(availHeight float64) []flowable {
	n, h := 0, 0.0
	for i, rh := range t.rowHeights {
		if h+rh > availHeight {
			break
		}
		n = i + 1
		h += rh
	}
	if n <= t.repeatRows {
		return nil
	}
	lim := t.nrows()
	if n == lim {
		return []flowable{t}
	}
	lines := t.splitLines(n)
	part := func(cells [][]tableCell, styles [][]*cellStyle, heights []float64, repeat int) *table {
		return &table{cells: cells, styles: styles, argW: t.colWidths, argH: heights, repeatRows: repeat, align: t.align}
	}
	first := part(t.cells[:n], t.styles[:n], t.rowHeights[:n], t.repeatRows)
	for _, cmd := range lines {
		if sr, er, ok := rangeFirst(n, lim, cmd.sr, cmd.er); ok {
			first.line(cmd.op, cmd.sc, sr, cmd.ec, er, cmd.weight, cmd.color)
		}
	}
	for _, cmd := range t.backs {
		if sr, er, ok := rangeFirst(n, lim, cmd.sr, cmd.er); ok {
			first.background(cmd.op, cmd.sc, sr, cmd.ec, er, cmd.colors...)
		}
	}
	var rest *table
	if repeat := t.repeatRows; repeat > 0 {
		cells := append(append([][]tableCell{}, t.cells[:repeat]...), t.cells[n:]...)
		styles := append(append([][]*cellStyle{}, t.styles[:repeat]...), t.styles[n:]...)
		heights := append(append([]float64{}, t.rowHeights[:repeat]...), t.rowHeights[n:]...)
		rest = part(cells, styles, heights, repeat)
		for _, cmd := range lines {
			for _, rows := range rangeRepeat(n, lim, repeat, cmd.sr, cmd.er) {
				rest.line(cmd.op, cmd.sc, rows[0], cmd.ec, rows[1], cmd.weight, cmd.color)
			}
		}
		for _, cmd := range t.backs {
			for _, rows := range rangeRepeat(n, lim, repeat, cmd.sr, cmd.er) {
				rest.background(cmd.op, cmd.sc, rows[0], cmd.ec, rows[1], cmd.colors...)
			}
		}
	} else {
		rest = part(t.cells[n:], t.styles[n:], t.rowHeights[n:], 0)
		for _, cmd := range lines {
			if sr, er, ok := rangeRest(n, cmd.sr, cmd.er); ok {
				rest.line(cmd.op, cmd.sc, sr, cmd.ec, er, cmd.weight, cmd.color)
			}
		}
		for _, cmd := range t.backs {
			if sr, er, ok := rangeRest(n, cmd.sr, cmd.er); ok {
				rest.background(cmd.op, cmd.sc, sr, cmd.ec, er, cmd.colors...)
			}
		}
	}
	rest.after = t.after
	return []flowable{first, rest}
}

func (t *table) split(c *canvas, availWidth, availHeight float64) []flowable {
	_ = t.calc(c, availWidth)
	return t.splitRows(availHeight)
}

func (t *table) draw(c *canvas) {
	c.saveState()
	t.drawBackgrounds(c)
	var current *cellStyle
	for i, row := range t.cells {
		rowpos, rowheight := t.rowPositions[i+1], t.rowHeights[i]
		for j, cell := range row {
			s := t.styles[i][j]
			if current == nil || s.color != current.color {
				c.setFillColor(s.color)
			}
			if current == nil || s.leading != current.leading || s.fontName != current.fontName || s.fontSize != current.fontSize {
				c.setFont(s.fontName, s.fontSize, s.leading)
			}
			current = s
			t.drawCell(c, cell, s, t.colPositions[j], rowpos, t.colWidths[j], rowheight)
		}
	}
	t.drawLines(c)
	c.restoreState()
}

func (t *table) drawBackgrounds(c *canvas) {
	nrows, ncols := t.nrows(), t.ncols()
	for _, cmd := range t.backs {
		sc, sr, ec, er := t.normRange(cmd.sc, cmd.sr, cmd.ec, cmd.er)
		x0, y0 := t.colPositions[sc], t.rowPositions[sr]
		x1, y1 := t.colPositions[min(ec+1, ncols)], t.rowPositions[min(er+1, nrows)]
		w, h := x1-x0, y1-y0
		if cmd.op == "ROWBACKGROUNDS" {
			for i := 0; i < er-sr+1; i++ {
				col := cmd.colors[i%len(cmd.colors)]
				h = t.rowHeights[sr+i]
				if !col.none {
					c.setFillColor(col)
					c.fillRect(x0, y0, w, -h)
				}
				y0 -= h
			}
			continue
		}
		if !cmd.colors[0].none {
			c.setFillColor(cmd.colors[0])
			c.fillRect(x0, y0, w, h)
		}
	}
}

func (t *table) drawCell(c *canvas, cell tableCell, s *cellStyle, colpos, rowpos, colwidth, rowheight float64) {
	if cell.list {
		var widths, heights []float64
		_, h := listCellGeom(c, cell.flows, colwidth, s, rowheight, &widths, &heights)
		var y float64
		switch s.valign {
		case "TOP":
			y = rowpos + rowheight - s.topPadding
		case "BOTTOM":
			y = rowpos + s.bottomPadding + h
		default:
			y = rowpos + (rowheight+s.bottomPadding-s.topPadding+h)/2.0
		}
		if len(cell.flows) > 0 {
			y += cell.flows[0].spaceBefore()
		}
		for i, v := range cell.flows {
			w, h := widths[i], heights[i]
			var x float64
			switch s.alignment {
			case "LEFT":
				x = colpos + s.leftPadding
			case "RIGHT":
				x = colpos + colwidth - s.rightPadding - w
			default:
				x = colpos + (colwidth+s.leftPadding-s.rightPadding-w)/2.0
			}
			y -= v.spaceBefore()
			y -= h
			drawOn(c, v, x, y, 0)
			y -= v.spaceAfter()
		}
		return
	}
	vals := cellLines(cell)
	n := float64(len(vals))
	var y float64
	switch s.valign {
	case "BOTTOM":
		y = rowpos + s.bottomPadding + n*s.leading - s.fontSize
	case "TOP":
		y = rowpos + rowheight - s.topPadding - s.fontSize
	default:
		y = rowpos + (s.bottomPadding+rowheight-s.topPadding+n*s.leading)/2.0 - s.fontSize
	}
	for _, v := range vals {
		switch s.alignment {
		case "LEFT":
			c.drawString(colpos+s.leftPadding, y, v)
		case "RIGHT":
			c.drawRightString(colpos+colwidth-s.rightPadding, y, v)
		default:
			c.drawCentredString(colpos+(colwidth+s.leftPadding-s.rightPadding)*0.5, y, v)
		}
		y -= s.leading
	}
}

type lineState struct {
	c        *canvas
	t        *table
	color    *color
	weight   float64
	weighted bool
}

func (l *lineState) prep(weight float64, col color) {
	if l.color == nil || col != *l.color {
		l.c.setStrokeColor(col)
		l.color = &col
	}
	if weight != 0 && (!l.weighted || weight != l.weight) {
		l.c.setLineWidth(weight)
		l.weight, l.weighted = weight, true
	}
}

func (l *lineState) hLines(sc, sr, ec, er int, weight float64, col color) {
	t := l.t
	hi := min(ec+2, len(t.colPositions))
	if sc >= hi || sr > er || sr >= len(t.rowPositions) {
		return
	}
	ecp := t.colPositions[sc:hi]
	rp := t.rowPositions[sr:min(er+1, len(t.rowPositions))]
	if len(ecp) <= 1 || len(rp) < 1 {
		return
	}
	l.prep(weight, col)
	for _, y := range rp {
		l.c.line(ecp[0], y, ecp[len(ecp)-1], y)
	}
}

func (l *lineState) vLines(sc, sr, ec, er int, weight float64, col color) {
	t := l.t
	hi := min(er+2, len(t.rowPositions))
	if sr >= hi || sc > ec || sc >= len(t.colPositions) {
		return
	}
	erp := t.rowPositions[sr:hi]
	cp := t.colPositions[sc:min(ec+1, len(t.colPositions))]
	if len(erp) <= 1 || len(cp) < 1 {
		return
	}
	l.prep(weight, col)
	for _, x := range cp {
		l.c.line(x, erp[len(erp)-1], x, erp[0])
	}
}

func (l *lineState) box(sc, sr, ec, er int, weight float64, col color) {
	l.hLines(sc, sr, ec, sr, weight, col)
	l.hLines(sc, er+1, ec, er+1, weight, col)
	l.vLines(sc, sr, sc, er, weight, col)
	l.vLines(ec+1, sr, ec+1, er, weight, col)
}

func (l *lineState) innerGrid(sc, sr, ec, er int, weight float64, col color) {
	l.hLines(sc, sr+1, ec, er, weight, col)
	l.vLines(sc+1, sr, ec, er, weight, col)
}

func (t *table) drawLines(c *canvas) {
	c.saveState()
	state := &lineState{c: c, t: t}
	capSet, joinSet := false, false
	for _, cmd := range t.lines {
		if !capSet {
			c.setLineCap(1)
			capSet = true
		}
		if !joinSet {
			c.setLineJoin(1)
			joinSet = true
		}
		sc, sr, ec, er := t.normRange(cmd.sc, cmd.sr, cmd.ec, cmd.er)
		switch cmd.op {
		case "GRID":
			state.box(sc, sr, ec, er, cmd.weight, cmd.color)
			state.innerGrid(sc, sr, ec, er, cmd.weight, cmd.color)
		case "BOX":
			state.box(sc, sr, ec, er, cmd.weight, cmd.color)
		case "INNERGRID":
			state.innerGrid(sc, sr, ec, er, cmd.weight, cmd.color)
		case "LINEBELOW":
			state.hLines(sc, sr+1, ec, er+1, cmd.weight, cmd.color)
		case "LINEABOVE":
			state.hLines(sc, sr, ec, er, cmd.weight, cmd.color)
		case "LINEBEFORE":
			state.vLines(sc, sr, ec, er, cmd.weight, cmd.color)
		case "LINEAFTER":
			state.vLines(sc+1, sr, ec+1, er, cmd.weight, cmd.color)
		}
	}
	c.restoreState()
}
