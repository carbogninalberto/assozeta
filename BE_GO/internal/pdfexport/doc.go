package pdfexport

import "time"

// drawOn is Flowable.drawOn.
func drawOn(c *canvas, f flowable, x, y, spare float64) {
	if spare != 0 {
		switch f.hAlign() {
		case "CENTER":
			x += 0.5 * spare
		case "RIGHT":
			x += spare
		}
	}
	c.saveState()
	c.translate(x, y)
	f.draw(c)
	c.restoreState()
}

// pageBreak is platypus.PageBreak.
type pageBreak struct{}

func (pageBreak) wrap(*canvas, float64, float64) (float64, float64) { return 0, 0 }
func (pageBreak) split(*canvas, float64, float64) []flowable        { return nil }
func (pageBreak) draw(*canvas)                                      {}
func (pageBreak) spaceBefore() float64                              { return 0 }
func (pageBreak) spaceAfter() float64                               { return 0 }
func (pageBreak) hAlign() string                                    { return "" }

// spacer is platypus.Spacer(width, height).
type spacer struct{ width, height float64 }

func (s spacer) wrap(*canvas, float64, float64) (float64, float64) { return s.width, s.height }
func (spacer) split(*canvas, float64, float64) []flowable          { return nil }
func (spacer) draw(*canvas)                                        {}
func (spacer) spaceBefore() float64                                { return 0 }
func (spacer) spaceAfter() float64                                 { return 0 }
func (spacer) hAlign() string                                      { return "LEFT" }

// docTemplate is SimpleDocTemplate with one frame per page and the same
// callback on every page.
type docTemplate struct {
	pageW, pageH                   float64
	left, right, top, bottom       float64
	onPage                         func(c *canvas, page int)
	now                            time.Time
	c                              *canvas
	page                           int
	y, prevSpace                   float64
	atTop, inPage                  bool
	pageFlowables, emptyPages      int
	x1, y1p, availWidth, availHigh float64
}

func (d *docTemplate) beginPage() {
	d.page++
	d.inPage = true
	d.pageFlowables = 0
	d.onPage(d.c, d.page)
	// Frame(leftMargin, bottomMargin, width, height) with 6 points of padding.
	width := d.pageW - d.left - d.right
	height := d.pageH - d.bottom - d.top
	x2, y2 := d.left+width, d.bottom+height
	d.x1 = d.left + 6
	d.y1p = d.bottom + 6
	d.availWidth = x2 - d.left - 6 - 6
	d.availHigh = y2 - d.y1p - 6
	d.y = y2 - 6
	d.atTop = true
	d.prevSpace = 0
}

func (d *docTemplate) endPage() error {
	if d.pageFlowables == 0 {
		d.emptyPages++
	} else {
		d.emptyPages = 0
	}
	if d.emptyPages >= 10 {
		return ErrLayout
	}
	d.c.showPage()
	d.inPage = false
	return nil
}

func (d *docTemplate) before(f flowable) float64 {
	if d.atTop {
		return 0
	}
	s := f.spaceBefore() - d.prevSpace
	if s < 0 {
		s = 0
	}
	return s
}

// add is Frame._add.
func (d *docTemplate) add(f flowable) bool {
	y, p := d.y, d.y1p
	s := d.before(f)
	h := y - p - s
	if h <= 0 {
		return false
	}
	w, h := f.wrap(d.c, d.availWidth, h)
	h += s
	y -= h
	if y < p-fuzz {
		return false
	}
	sa := f.spaceAfter()
	drawOn(d.c, f, d.x1, y, d.availWidth-w)
	y -= sa
	d.prevSpace = sa
	if y != d.y {
		d.atTop = false
	}
	d.y = y
	return true
}

// build is BaseDocTemplate.build for tables, paragraphs, spacers and page
// breaks.
func (d *docTemplate) build(flowables []flowable) ([]byte, error) {
	d.c = newCanvas(d.pageW, d.pageH)
	postponed := map[flowable]bool{}
	for len(flowables) > 0 {
		if d.c.err != nil {
			return nil, d.c.err
		}
		if !d.inPage {
			d.beginPage()
		}
		f := flowables[0]
		flowables = flowables[1:]
		if _, ok := f.(pageBreak); ok {
			if err := d.endPage(); err != nil {
				return nil, err
			}
			continue
		}
		if d.add(f) {
			d.pageFlowables++
			continue
		}
		var parts []flowable
		if h := d.y - d.y1p - d.before(f); h > 0 {
			parts = f.split(d.c, d.availWidth, h)
		}
		if len(parts) > 0 {
			if !d.add(parts[0]) {
				return nil, ErrLayout
			}
			d.pageFlowables++
			flowables = append(append([]flowable{}, parts[1:]...), flowables...)
			continue
		}
		if postponed[f] {
			return nil, ErrLayout
		}
		postponed[f] = true
		flowables = append([]flowable{f}, flowables...)
		if err := d.endPage(); err != nil {
			return nil, err
		}
	}
	if d.c.err != nil {
		return nil, d.c.err
	}
	if d.inPage {
		if err := d.endPage(); err != nil {
			return nil, err
		}
	}
	return d.c.save(d.now), nil
}
