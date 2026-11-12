package pdfexport

import (
	"sort"
	"strconv"
	"strings"
)

// textObject is the part of PDFTextObject a paragraph uses.
type textObject struct {
	c          *canvas
	code       []string
	font       *font
	fontSize   float64
	leading    float64
	hasLeading bool
	wordSpace  float64
	x0         float64
}

// moveCursor is PDFTextObject.moveCursor(dx, 0): a move right after another
// one replaces it.
func (t *textObject) moveCursor(dx float64) {
	dy := 0.0
	t.x0 += dx
	if n := len(t.code); n > 0 && strings.HasSuffix(t.code[n-1], " Td") {
		if fields := strings.Fields(t.code[n-1]); len(fields) == 3 {
			lastDx, _ := strconv.ParseFloat(fields[0], 64)
			lastDy, _ := strconv.ParseFloat(fields[1], 64)
			t.code = t.code[:n-1]
			dx += lastDx
			dy -= lastDy
		}
	}
	t.code = append(t.code, fpStrs(dx, -dy)+" Td")
}

// setXPos is paragraph.setXPos.
func (t *textObject) setXPos(dx float64) {
	if dx > 1e-6 || dx < -1e-6 {
		t.moveCursor(dx)
	}
}

// textOut is PDFTextObject._textOut.
func (t *textObject) textOut(text string, newLine bool) {
	out := t.c.formatText(text, t.font, t.fontSize, t.leading)
	if newLine {
		out += " T*"
	}
	t.code = append(t.code, out)
}

func (t *textObject) setWordSpace(ws float64) {
	t.wordSpace = ws
	t.code = append(t.code, fpStr(ws)+" Tw")
}

func nbspCount(text string) int { return strings.Count(text, "\u00a0") }

// trailingSpaceLength is _trailingSpaceLength: the width of the white space
// that ends a text, in the font of the text object.
func trailingSpaceLength(text string, tx *textObject) float64 {
	end := len(text)
	for end > 0 {
		r, size := lastRune(text[:end])
		if !strings.ContainsRune(rlSpaces, r) {
			break
		}
		end -= size
	}
	if end == len(text) {
		return 0
	}
	return stringWidth(text[end:], tx.font, tx.fontSize)
}

func lastRune(s string) (rune, int) {
	runes := []rune(s)
	r := runes[len(runes)-1]
	return r, len(string(r))
}

// usConv is _usConv: a width, offset or gap in points. values holds the
// font sizes the 'L', 'F' and 'f' suffixes stand for.
func usConv(v usVal, values map[byte]float64, def float64) float64 {
	if v.isNum {
		return v.num
	}
	s := pyTrimASCII(v.str)
	if s == "" {
		if def != 0 {
			return def
		}
		panic(paraFailure{"unsupported operand type(s) for +: 'int' and 'str'"})
	}
	if idx := strings.LastIndex(s, "*"); idx >= 0 && !strings.Contains(s[:idx], "\n") && len(pyTrimASCII(s[idx+1:])) == 1 &&
		strings.Contains("PLfF", pyTrimASCII(s[idx+1:])) {
		scale, ok := values[pyTrimASCII(s[idx+1:])[0]]
		f, st := pyFloat(s[:idx])
		if !ok || st != attrOK {
			panic(paraFailure{"bad underline value"})
		}
		return f * scale
	}
	n, st := paraNum(s, false)
	if st != attrOK {
		panic(paraFailure{"bad underline value"})
	}
	return n.v
}

// doLine is _do_line.
func doLine(c *canvas, x1, y1, x2, y2, width float64, stroke color) {
	if width != c.state.lineWidth {
		c.setLineWidth(width)
	}
	if stroke != c.state.strokeColor {
		c.setStrokeColor(stroke)
	}
	c.line(x1, y1, x2, y2)
}

// drawUS draws the lines of one underline or strike: the repeats below each
// other.
func drawUS(c *canvas, l usLine, x1, x2, y0, defaultWidth float64, values map[byte]float64, textColor color) {
	underline := l.kind == lineUnderline
	lw := usConv(l.width, values, defaultWidth)
	lg := usConv(l.gap, values, 1)
	dy := lg + lw
	if !underline {
		dy = -dy
	}
	offset := l.offset
	if !offset.isNum && offset.str == "" {
		offset = usVal{str: "0.25*L"}
		if underline {
			offset = usVal{str: "-0.125*L"}
		}
	}
	y := y0 + l.rise + usConv(offset, values, 0)
	stroke := textColor
	if l.hasColor {
		stroke = l.color
	}
	for m := l.mult; m > 0; m-- {
		doLine(c, x1, y, x2, y, lw, stroke)
		y -= dy
	}
}

// usState is an entry of xs.us_lines while a line is drawn.
type usState struct {
	line  usLine
	size  float64
	color color
	x1    float64
	max   float64
	x2    float64
}

// linkState is an entry of xs.links while a line is drawn.
type linkState struct {
	ref    linkRef
	x1     float64
	lo, hi float64
	x2     float64
}

func containsLink(list []linkRef, l linkRef) bool {
	for _, item := range list {
		if item == l {
			return true
		}
	}
	return false
}

// doLink is _doLink for a link whose target linkTarget accepts.
func doLink(c *canvas, target string, rect [4]float64) {
	kind, to, _ := linkTarget(target)
	if kind == "" {
		return
	}
	c.linkURL(to, rect, kind)
}

// fragState is tx.XtraState of the several-fragment layout.
type fragState struct {
	curY       float64
	textColor  *color
	rise       float64
	us         map[usLine]*usState
	links      map[linkRef]*linkState
	backColor  *color
	backX      float64
	backColors []backRect
	first      *fragLine
	// defaultWidth is tx._defaultLineWidth: the canvas line width when the
	// text object was made.
	defaultWidth float64
}

type backRect struct {
	x1, x2 float64
	color  color
}

func containsUS(list []usLine, l usLine) bool {
	for _, item := range list {
		if item == l {
			return true
		}
	}
	return false
}

// putFragLine is _putFragLine for the lines without images or links.
func (p *paragraph) putFragLine(tx *textObject, xs *fragState, curX float64, line *fragLine) {
	c := tx.c
	leading := p.style.leading
	if !tx.hasLeading || tx.leading != leading {
		tx.hasLeading, tx.leading = true, leading
		tx.code = append(tx.code, fpStr(leading)+" TL")
	}
	ws := tx.wordSpace
	x0 := tx.x0
	nSpaces := 0
	var live []usLine
	var liveLinks []linkRef
	words := line.words
	for i, f := range words {
		if f.img != nil {
			size := tx.fontSize
			if tx.font == nil {
				size = p.style.fontSize
			}
			w, h := f.img.width.norm(p.width), f.img.height.norm(size)
			iy0, _ := imgVRange(h, f.img.valign, size)
			curXS := curX + float64(nSpaces)*ws
			c.drawImageAt(f.img.image, curXS, xs.curY+iy0, w, h)
			curX += w
			curXS += w
			tx.setXPos(curXS - tx.x0)
			if i == len(words)-1 {
				if tx.font == nil {
					tx.font, tx.fontSize, tx.leading, tx.hasLeading = fontsByName[p.style.fontName], p.style.fontSize, p.style.fontSize*1.2, true
					tx.code = append(tx.code, c.fontRef(tx.font)+" "+fpStr(tx.fontSize)+" Tf "+fpStr(tx.leading)+" TL")
				}
				tx.textOut("", true)
			}
			continue
		}
		curXS := curX + float64(nSpaces)*ws
		endX := curXS
		size := f.size
		if i > 0 {
			endX = curXS - trailingSpaceLength(words[i-1].text, tx)
		}
		if tx.font != f.font || tx.fontSize != size {
			tx.font, tx.fontSize = f.font, size
			tx.code = append(tx.code, c.fontRef(f.font)+" "+fpStr(size)+" Tf")
		}
		if xs.textColor == nil || *xs.textColor != f.color {
			col := f.color
			xs.textColor = &col
			tx.code = append(tx.code, fpStrs(col.r, col.g, col.b)+" rg")
		}
		if xs.rise != f.rise {
			xs.rise = f.rise
			tx.code = append(tx.code, fpStr(f.rise)+" Ts")
		}
		changed := !sameUS(live, f.us)
		if changed {
			for _, l := range live {
				if !containsUS(f.us, l) {
					xs.us[l].x2 = endX
				}
			}
		}
		linksChanged := !sameLinks(liveLinks, f.links)
		if linksChanged {
			for _, l := range liveLinks {
				if !containsLink(f.links, l) {
					xs.links[l].x2 = endX
				}
			}
		}
		tx.textOut(f.text, i == len(words)-1)
		if changed {
			for _, l := range f.us {
				if !containsUS(live, l) {
					xs.us[l] = &usState{line: l, size: size, color: f.color, x1: curXS, max: size}
				}
			}
			live = f.us
		}
		for _, l := range live {
			if state := xs.us[l]; size > state.max {
				state.max = size
			}
		}
		lo, hi := f.rise-0.2*size, f.rise+size
		if linksChanged {
			for _, l := range f.links {
				if !containsLink(liveLinks, l) {
					xs.links[l] = &linkState{ref: l, x1: curX, lo: lo, hi: hi}
				}
			}
			liveLinks = f.links
		}
		for _, l := range liveLinks {
			if state := xs.links[l]; lo < state.lo || hi > state.hi {
				state.lo, state.hi = min(lo, state.lo), max(hi, state.hi)
			}
		}
		bg := f.back
		switch {
		case bg != nil && xs.backColor == nil:
			xs.backColor, xs.backX = bg, curXS
		case xs.backColor != nil:
			if bg == nil {
				xs.backColors = append(xs.backColors, backRect{xs.backX, endX, *xs.backColor})
				xs.backColor = nil
			} else if *f.back != *xs.backColor || *xs.textColor != *xs.backColor {
				xs.backColors = append(xs.backColors, backRect{xs.backX, endX, *xs.backColor})
				xs.backColor, xs.backX = bg, curXS
			}
		}
		curX += stringWidth(f.text, tx.font, tx.fontSize)
		nSpaces += strings.Count(f.text, " ") + nbspCount(f.text)
	}
	curXS := curX + float64(nSpaces-1)*ws
	for _, l := range live {
		xs.us[l].x2 = curXS
	}
	for _, l := range liveLinks {
		xs.links[l].x2 = curXS
	}
	if xs.backColor != nil {
		xs.backColors = append(xs.backColors, backRect{xs.backX, curXS, *xs.backColor})
	}
	if tx.x0 != x0 {
		tx.setXPos(x0 - tx.x0)
	}
}

// postText is _do_post_text: the backgrounds, links and lines of the line
// just drawn, and the move to the next one.
func (p *paragraph) postText(tx *textObject, xs *fragState) {
	c := tx.c
	y0 := xs.curY
	size := xs.first.fontSize
	leading := p.style.leading
	if len(xs.backColors) > 0 {
		ydesc := y0 + size - leading
		for _, r := range xs.backColors {
			c.setFillColor(r.color)
			c.fillRect(r.x1, ydesc, r.x2-r.x1, leading)
		}
		xs.backColors = nil
		xs.backColor = nil
	}
	if len(xs.links) > 0 {
		states := make([]*linkState, 0, len(xs.links))
		for _, state := range xs.links {
			states = append(states, state)
		}
		sort.Slice(states, func(i, j int) bool { return states[i].ref.n < states[j].ref.n })
		for _, state := range states {
			doLink(c, state.ref.target, [4]float64{state.x1, y0 + state.lo, state.x2, y0 + state.hi})
		}
		xs.links = map[linkRef]*linkState{}
	}
	if len(xs.us) > 0 {
		states := make([]*usState, 0, len(xs.us))
		for _, state := range xs.us {
			states = append(states, state)
		}
		sort.Slice(states, func(i, j int) bool { return states[i].line.n < states[j].line.n })
		for _, state := range states {
			values := map[byte]float64{'L': size, 'f': state.size, 'F': state.max}
			drawUS(c, state.line, state.x1, state.x2, y0, xs.defaultWidth, values, state.color)
		}
		xs.us = map[usLine]*usState{}
	}
	xs.curY -= leading
}

// draw is Paragraph.drawPara for left-aligned and centred lines.
func (p *paragraph) draw(c *canvas) {
	if bg := p.style.backColor; bg != nil {
		c.saveState()
		c.setFillColor(*bg)
		c.fillRect(0, 0, p.width, p.height)
		c.restoreState()
	}
	if p.lineCount() == 0 {
		return
	}
	c.saveState()
	leading := p.style.leading
	tx := &textObject{c: c}
	defaultWidth := c.state.lineWidth
	if p.kind == 0 {
		f := p.lineFrag
		curY := p.height - f.size
		c.setFillColor(f.color)
		tx.font, tx.fontSize, tx.leading = f.font, f.size, leading
		tx.code = []string{"BT", "1 0 0 1 " + fpStrs(0, curY) + " Tm", c.fontRef(f.font) + " " + fpStr(f.size) + " Tf " + fpStr(leading) + " TL"}
		for i, line := range p.lines {
			texts := make([]string, len(line.words))
			for j, word := range line.words {
				texts[j] = string(word.text)
			}
			text := strings.Join(texts, " ")
			simple := line.extra > -1e-8
			spaces := 0
			if !simple {
				spaces = len(line.words) + nbspCount(text) - 1
				simple = spaces <= 0
			}
			offset := 0.0
			if simple {
				switch p.style.alignment() {
				case alignCenter:
					offset = 0 + 0.5*line.extra
				case alignRight:
					offset = 0 + line.extra
				}
				tx.setXPos(offset)
				tx.textOut(text, true)
				tx.setXPos(-offset)
			} else {
				tx.code = append(tx.code, fpStr(line.extra/float64(spaces))+" Tw")
				tx.textOut(text, true)
				tx.code = append(tx.code, "0 Tw")
			}
			if len(f.us) > 0 {
				values := map[byte]float64{'L': f.size, 'F': f.size, 'f': f.size}
				x1 := offset
				x2 := x1 + stringWidth(text, f.font, f.size)
				for _, l := range f.us {
					drawUS(c, l, x1, x2, curY-float64(i)*leading, defaultWidth, values, f.color)
				}
			}
			if len(f.links) > 0 {
				y := curY - float64(i)*leading - f.size/8.0
				width := stringWidth(text, f.font, f.size)
				for _, l := range f.links {
					doLink(c, l.target, [4]float64{offset, y, offset + width, y + leading})
				}
			}
		}
	} else {
		xs := &fragState{us: map[usLine]*usState{}, links: map[linkRef]*linkState{}, first: p.fragLines[0], defaultWidth: defaultWidth}
		xs.curY = p.height - xs.first.fontSize
		tx.code = []string{"BT", "1 0 0 1 " + fpStrs(0, xs.curY) + " Tm"}
		for _, line := range p.fragLines {
			simple := line.extra > -1e-8
			spaces := 0
			if !simple {
				spaces = line.wordCount - 1
				for _, word := range line.words {
					spaces += nbspCount(word.text)
				}
				simple = spaces <= 0
			}
			m := 0.0
			if simple {
				switch p.style.alignment() {
				case alignCenter:
					m = 0 + 0.5*line.extra
				case alignRight:
					m = 0 + line.extra
				}
				tx.setXPos(m)
				p.putFragLine(tx, xs, m, line)
			} else {
				tx.setWordSpace(line.extra / float64(spaces))
				p.putFragLine(tx, xs, 0, line)
				tx.setWordSpace(0)
			}
			tx.setXPos(-m)
			p.postText(tx, xs)
		}
	}
	c.add(strings.Join(append(tx.code, "ET"), " "))
	c.restoreState()
}
