package pdfexport

import (
	"math"
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
	// olb and oleading are _olb and _oleading of the autoLeading layout: the
	// bottom of the line before, and the leading it was drawn with.
	olb      *float64
	oleading float64
	// dotsOffsetX is _dotsOffsetX, set while a centred line draws its
	// endDots.
	dotsOffsetX *float64
	// xOffset is _x_offset: the offset of the line a _xxxDrawParaLineX draws,
	// which the endDots of a right-to-left line start from.
	xOffset float64
}

// moveCursor is PDFTextObject.moveCursor(dx, 0): a move right after another
// one replaces it.
func (t *textObject) moveCursor(dx float64) {
	dy := 0.0
	if n := len(t.code); n > 0 && strings.HasSuffix(t.code[n-1], " Td") {
		if fields := strings.Fields(t.code[n-1]); len(fields) == 3 {
			lastDx, _ := strconv.ParseFloat(fields[0], 64)
			lastDy, _ := strconv.ParseFloat(fields[1], 64)
			t.code = t.code[:n-1]
			dx += lastDx
			dy -= lastDy
			t.x0 -= lastDx
		}
	}
	t.x0 += dx
	t.code = append(t.code, fpStrs(dx, -dy)+" Td")
}

// setXPos is paragraph.setXPos.
func (t *textObject) setXPos(dx float64) {
	if dx > 1e-6 || dx < -1e-6 {
		t.moveCursor(dx)
	}
}

// setTextOrigin is PDFTextObject.setTextOrigin.
func (t *textObject) setTextOrigin(x, y float64) {
	t.code = append(t.code, "1 0 0 1 "+fpStrs(x, y)+" Tm")
	t.x0 = x
}

// setFont is PDFTextObject.setFont with the leading it defaults to.
func (t *textObject) setFont(name string, size float64) {
	t.font, t.fontSize, t.leading, t.hasLeading = fontsByName[name], size, size*1.2, true
	t.code = append(t.code, t.c.fontRef(t.font)+" "+fpStr(size)+" Tf "+fpStr(t.leading)+" TL")
}

// textOut is PDFTextObject._textOut.
func (t *textObject) textOut(text string, newLine bool) {
	out := t.c.formatText(text, t.font, t.fontSize, t.leading)
	if newLine {
		out += " T*"
	}
	t.code = append(t.code, out)
}

// setFillColor is PDFTextObject.setFillColor: the fill colour operators, then
// the fill alpha of the colour through the canvas's ExtGState.
func (t *textObject) setFillColor(col color) {
	t.c.setColorOperators(col, false, &t.code)
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
	return n.v.float()
}

// doLine is _do_line.
func doLine(c *canvas, x1, y1, x2, y2, width float64, stroke color) {
	if width != c.state.lineWidth {
		c.setLineWidth(width)
	}
	if !colorEq(stroke, c.state.strokeColor) {
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
	y := y0 + l.rise.float() + usConv(offset, values, 0)
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

// doLink is _doLink.
func doLink(c *canvas, target string, rect [4]float64) {
	kind, to := linkTarget(target)
	switch kind {
	case "":
		return
	case "internal":
		c.linkRect(to, rect)
	default:
		c.linkURL(to, rect, kind)
	}
}

// fragState is tx.XtraState of the several-fragment layout.
type fragState struct {
	curY       pyNum
	textColor  *color
	rise       float64
	us         map[string]*usState
	links      map[linkRef]*linkState
	backColor  *color
	backX      float64
	backColors []backRect
	first      *fragLine
	// style, leftIndent, autoLeading, leading and paraWidth are xs.style,
	// xs.leftIndent, xs.autoLeading, xs.leading and xs.paraWidth.
	style       paraStyle
	leftIndent  float64
	autoLeading string
	leading     float64
	paraWidth   float64
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
		if usLineEq(item, l) {
			return true
		}
	}
	return false
}

// lineNBSP is sum(_nbspCount(w.text) for w in line.words if not hasattr(w,'cbDefn')).
func lineNBSP(line *fragLine) int {
	n := 0
	for _, w := range line.words {
		if w.img == nil && w.anchor == nil {
			n += nbspCount(w.text)
		}
	}
	return n
}

// putFragLine is _putFragLine: draws one line of the several-fragment layout.
func (p *paragraph) putFragLine(tx *textObject, xs *fragState, curX float64, line *fragLine, last bool, pKind string) {
	c := tx.c
	curY := xs.curY
	x0 := tx.x0
	autoLeading := xs.autoLeading
	leading := xs.leading
	curX += xs.leftIndent
	dal := autoLeading == "min" || autoLeading == "max"
	var ascent, descent float64
	if dal {
		if autoLeading == "max" {
			ascent = pyMax(fiveSixths*leading, line.ascent)
			descent = pyMax(oneSixth*leading, -line.descent)
		} else {
			ascent = line.ascent
			descent = -line.descent
		}
		leading = ascent + descent
	}
	if !tx.hasLeading || tx.leading != leading {
		tx.leading, tx.hasLeading = leading, true
		tx.code = append(tx.code, fpStr(leading)+" TL")
	}
	if dal {
		if tx.olb != nil {
			xcy := *tx.olb - ascent
			if tx.oleading != leading {
				curY = numAddOrErr(curY, numFloat(leading-tx.oleading))
			}
			if math.Abs(numSubOrErr(numFloat(xcy), curY).float()) > 1e-8 {
				curY = numFloat(xcy)
				tx.setTextOrigin(x0, xcy)
				xs.curY = curY
			}
		}
		olb := curY.float() - descent
		tx.olb, tx.oleading = &olb, leading
	}
	ws := tx.wordSpace
	nSpaces := 0
	words := line.words
	var live []usLine
	var liveLinks []linkRef
	for i, f := range words {
		if f.anchor != nil {
			// _putFragLine's kind == 'anchor': a bookmark at the line's own
			// position, without moving the cursor.
			c.bookmarkHorizontal(f.anchor.name, curX, curY.float()+leading)
			if i == len(words)-1 {
				if tx.font == nil {
					tx.font, tx.fontSize = fontsByName[xs.style.fontName], xs.style.fontSize.float()
					tx.leading, tx.hasLeading = tx.fontSize*1.2, true
					tx.code = append(tx.code, c.fontRef(tx.font)+" "+fpStr(tx.fontSize)+" Tf "+fpStr(tx.leading)+" TL")
				}
				tx.textOut("", true)
			}
			continue
		}
		if f.img != nil {
			sizeNum := numFloat(tx.fontSize)
			if tx.font == nil {
				sizeNum = xs.style.fontSize
			}
			w := f.img.width.norm(numFloat(xs.paraWidth))
			h := f.img.height.norm(sizeNum)
			iy0, _ := imgVRange(h, f.img.valign, sizeNum)
			curXS := curX + float64(nSpaces)*ws
			c.drawImageAt(f.img.image, curXS, curY.float()+iy0, w, h)
			curX += w
			curXS += w
			tx.setXPos(curXS - tx.x0)
			if i == len(words)-1 {
				if tx.font == nil {
					tx.font, tx.fontSize = fontsByName[xs.style.fontName], xs.style.fontSize.float()
					tx.leading, tx.hasLeading = tx.fontSize*1.2, true
					tx.code = append(tx.code, c.fontRef(tx.font)+" "+fpStr(tx.fontSize)+" Tf "+fpStr(tx.leading)+" TL")
				}
				tx.textOut("", true)
			}
			continue
		}
		curXS := curX + float64(nSpaces)*ws
		endX := curXS
		size := f.size.float()
		if i > 0 {
			endX = curXS - trailingSpaceLength(words[i-1].text, tx)
		}
		if tx.font != f.font || tx.fontSize != size {
			tx.font, tx.fontSize = f.font, size
			tx.code = append(tx.code, c.fontRef(f.font)+" "+fpStr(size)+" Tf")
		}
		if xs.textColor == nil || !colorEq(*xs.textColor, f.color) {
			col := f.color
			xs.textColor = &col
			tx.setFillColor(col)
		}
		rise := f.rise.float()
		if xs.rise != rise {
			xs.rise = rise
			tx.code = append(tx.code, fpStr(rise)+" Ts")
		}
		changed := !sameUS(live, f.us)
		if changed {
			for _, l := range live {
				if !containsUS(f.us, l) {
					xs.us[usLineKey(l)].x2 = endX
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
					xs.us[usLineKey(l)] = &usState{line: l, size: size, color: f.color, x1: curXS, max: size}
				}
			}
			live = f.us
		}
		for _, l := range live {
			if state := xs.us[usLineKey(l)]; size > state.max {
				state.max = size
			}
		}
		lo, hi := rise-0.2*size, rise+size
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
				state.lo, state.hi = pyMin(lo, state.lo), pyMax(hi, state.hi)
			}
		}
		bg := f.back
		// The Python test is truthiness: a colour value that is an empty
		// sequence or a zero is no background.
		hasBg := bg != nil && colorTruthy(*bg)
		switch {
		case hasBg && xs.backColor == nil:
			xs.backColor, xs.backX = bg, curXS
		case xs.backColor != nil:
			if !hasBg {
				xs.backColors = append(xs.backColors, backRect{xs.backX, endX, *xs.backColor})
				xs.backColor = nil
			} else if !colorEq(*f.back, *xs.backColor) || !colorEq(*xs.textColor, *xs.backColor) {
				xs.backColors = append(xs.backColors, backRect{xs.backX, endX, *xs.backColor})
				xs.backColor, xs.backX = bg, curXS
			}
		}
		curX += stringWidth(f.text, tx.font, tx.fontSize)
		nSpaces += strings.Count(f.text, " ") + nbspCount(f.text)
	}
	curXS := curX + float64(nSpaces-1)*ws
	if last && xs.style.endDots != "" {
		if xs.style.wordWrap != "RTL" {
			// Dots run left to right.
			if pKind != "right" {
				p.doDotsFrag(curX, curXS, line.maxWidth, xs, tx, true)
			}
		} else if pKind != "left" {
			start := tx.xOffset
			p.doDotsFrag(start, start, tx.x0-start, xs, tx, false)
		}
	}
	for _, l := range live {
		xs.us[usLineKey(l)].x2 = curXS
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

// doDotsFrag is _do_dots_frag: the endDots that fill the rest of the line. A
// right-to-left line fills the space before the text (left false).
func (p *paragraph) doDotsFrag(curX, curXS, maxWidth float64, xs *fragState, tx *textObject, left bool) {
	style := xs.style
	text := style.endDots
	f := fontsByName[style.fontName]
	fontSize := style.fontSize.float()
	txtlen := stringWidth(text, f, fontSize)
	if curXS+txtlen > maxWidth {
		return
	}
	if tx.font != f || tx.fontSize != fontSize {
		tx.setFont(style.fontName, fontSize)
	}
	if left {
		if tx.dotsOffsetX != nil {
			maxWidth += *tx.dotsOffsetX
		} else {
			maxWidth += tx.x0
		}
	}
	tx.setTextOrigin(0, xs.curY.float())
	tx.setXPos(curXS - curX)
	n := int((maxWidth - curXS) / txtlen)
	tx.setXPos(maxWidth - txtlen*float64(n))
	if xs.textColor == nil || !colorEq(*xs.textColor, style.textColor) {
		tx.setFillColor(style.textColor)
	}
	if style.backColor != nil {
		xs.backColors = append(xs.backColors, backRect{curX, maxWidth, *style.backColor})
	}
	tx.textOut(strings.Repeat(text, n), true)
}

// postText is _do_post_text: the backgrounds, links and lines of the line
// just drawn, and the move to the next one.
func (p *paragraph) postText(tx *textObject, xs *fragState) {
	c := tx.c
	y0 := xs.curY
	size := xs.first.fontSize
	leading := p.style.leading
	switch xs.autoLeading {
	case "max":
		leading = numMax(leading, numMulOrErr(numFloat(1.2), size))
	case "min":
		leading = numMulOrErr(numFloat(1.2), size)
	}
	if len(xs.backColors) > 0 {
		yl := numAddOrErr(y0, size)
		ydesc := numSubOrErr(yl, leading)
		for _, r := range xs.backColors {
			c.setFillColor(r.color)
			c.fillRect(r.x1, ydesc.float(), r.x2-r.x1, leading.float())
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
			doLink(c, state.ref.target, [4]float64{state.x1, y0.float() + state.lo, state.x2, y0.float() + state.hi})
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
			values := map[byte]float64{'L': size.float(), 'f': state.size, 'F': state.max}
			drawUS(c, state.line, state.x1, state.x2, y0.float(), xs.defaultWidth, values, state.color)
		}
		xs.us = map[string]*usState{}
	}
	xs.curY = numSubOrErr(xs.curY, leading)
}

// bulletFont is pdfmetrics.getFont(style.bulletFontName): a name that is no
// registered font raises, as its KeyError does.
func bulletFont(name string) *font {
	f, ok := fontsByName[name]
	if !ok {
		panic(paraFailure{"KeyError " + pythonReprChar(name)})
	}
	return f
}

// drawBullet is _drawBullet: draws the bullet at the left indent and answers
// the offset the first line starts at, which a wide bullet moves right. A
// right-to-left paragraph places the bullet from the right indent instead and
// keeps the offset.
func (p *paragraph) drawBullet(c *canvas, offset, curY float64, rtl bool) float64 {
	style := p.style
	b := p.bullet
	anchor := style.bulletAnchor
	bulletSize := style.bulletFontSize.float()
	bulletWidth := 0.0
	if rtl || anchor != "start" {
		numeric := anchor == "numeric"
		if b.isText() {
			t := b.text
			q := numeric && strings.Contains(t, ".")
			if q {
				t = t[:strings.Index(t, ".")]
			}
			bulletWidth = stringWidth(t, bulletFont(style.bulletFontName), bulletSize)
			if q {
				bulletWidth += 0.5 * stringWidth(".", bulletFont(style.bulletFontName), bulletSize)
			}
		} else {
			bulletWidth = 0
			for _, f := range b.frags {
				t := f.text
				q := numeric && strings.Contains(t, ".")
				if q {
					t = t[:strings.Index(t, ".")]
					bulletWidth += 0.5 * stringWidth(".", f.font, f.size.float())
				}
				bulletWidth += stringWidth(t, f.font, f.size.float())
				if q {
					break
				}
			}
		}
	}
	if anchor == "middle" {
		bulletWidth *= 0.5
	}
	curY += style.bulletOffsetY.float()
	bf := bulletFont(style.bulletFontName)
	var startX float64
	if !rtl {
		startX = numSubOrErr(style.bulletIndent, numFloat(bulletWidth)).float()
	} else {
		width := p.wrapWidths[0]
		startX = numAddOrErr(numFloat(width), style.rightIndent).float() -
			numAddOrErr(style.bulletIndent, numFloat(bulletWidth)).float()
	}
	tx := &textObject{c: c}
	tx.code = []string{"BT"}
	tx.setTextOrigin(startX, curY)
	tx.font, tx.fontSize, tx.leading, tx.hasLeading = bf, bulletSize, bulletSize*1.2, true
	tx.code = append(tx.code, c.fontRef(bf)+" "+fpStr(tx.fontSize)+" Tf "+fpStr(tx.leading)+" TL")
	col := style.textColor
	if style.bulletColor != nil {
		col = *style.bulletColor
	}
	tx.setFillColor(col)
	endX := startX
	if b.isText() {
		tx.textOut(b.text, false)
		endX += stringWidth(b.text, tx.font, tx.fontSize)
	} else {
		for _, f := range b.frags {
			tx.setFont(f.font.name, f.size.float())
			tx.setFillColor(f.color)
			tx.textOut(f.text, false)
			endX += stringWidth(f.text, f.font, f.size.float())
		}
	}
	c.add(strings.Join(append(tx.code, "ET"), " "))
	if rtl {
		return offset
	}
	bulletEnd := numAddOrErr(numFloat(endX), numMulOrErr(numFloat(0.6), style.bulletFontSize))
	return numMax(numFloat(offset), numSubOrErr(bulletEnd, style.leftIndent)).float()
}

// rect is canvas.rect(x, y, w, h, stroke, fill).
func (c *canvas) rect(x, y, w, h float64, stroke, fill bool) {
	op := "n"
	switch {
	case stroke && fill:
		op = "B*"
	case fill:
		op = "f*"
	case stroke:
		op = "S"
	}
	c.add("n " + fpStrs(x, y, w, h) + " re " + op)
}

// roundRect is canvas.roundRect: the path operations of PDFPathObject.roundRect
// for one radius, closed and stroked or filled.
func (c *canvas) roundRect(x, y, w, h, radius float64, stroke, fill bool) {
	const m = 0.4472 // radius multiplier
	xlo, xhi := pyMin(x, x+w), pyMax(x, x+w)
	ylo, yhi := pyMin(y, y+h), pyMax(y, y+h)
	t := m * radius
	add := func(line string) { c.add(line) }
	add("n")
	add(fpStrs(xlo+radius, ylo) + " m")
	add(fpStrs(xhi-radius, ylo) + " l")
	add(fpStrs(xhi-t, ylo, xhi, ylo+t, xhi, ylo+radius) + " c")
	add(fpStrs(xhi, yhi-radius) + " l")
	add(fpStrs(xhi, yhi-t, xhi-t, yhi, xhi-radius, yhi) + " c")
	add(fpStrs(xlo+radius, yhi) + " l")
	add(fpStrs(xlo+t, yhi, xlo, yhi-t, xlo, yhi-radius) + " c")
	add(fpStrs(xlo, ylo+radius) + " l")
	add(fpStrs(xlo, ylo+t, xlo+t, ylo, xlo+radius, ylo) + " c")
	add("h")
	op := "n"
	switch {
	case stroke && fill:
		op = "B*"
	case fill:
		op = "f*"
	case stroke:
		op = "S"
	}
	add(op)
}

// simpleText is ' '.join(words) of the one-font layout.
func simpleText(words []*simpleWord) string {
	texts := make([]string, len(words))
	for i, word := range words {
		texts[i] = string(word.text)
	}
	return strings.Join(texts, " ")
}

// leftDrawParaLine is _leftDrawParaLine.
func leftDrawParaLine(tx *textObject, offset, extraspace float64, text string, words int, last bool) float64 {
	simple := extraspace > -1e-8
	tx.setXPos(offset)
	nSpaces := 0
	if !simple {
		nSpaces = words + nbspCount(text) - 1
		simple = nSpaces <= 0
	}
	if simple {
		tx.textOut(text, true)
	} else {
		tx.setWordSpace(extraspace / float64(nSpaces))
		tx.textOut(text, true)
		tx.setWordSpace(0)
	}
	tx.setXPos(-offset)
	return offset
}

// centerDrawParaLine is _centerDrawParaLine.
func centerDrawParaLine(tx *textObject, offset, extraspace float64, text string, words int, last bool) float64 {
	simple := extraspace > -1e-8
	nSpaces := 0
	if !simple {
		nSpaces = words + nbspCount(text) - 1
		simple = nSpaces <= 0
	}
	var m float64
	if simple {
		m = offset + 0.5*extraspace
		tx.setXPos(m)
		tx.textOut(text, true)
	} else {
		m = offset
		tx.setWordSpace(extraspace / float64(nSpaces))
		tx.setXPos(m)
		tx.textOut(text, true)
		tx.setWordSpace(0)
	}
	tx.setXPos(-m)
	return m
}

// rightDrawParaLine is _rightDrawParaLine.
func rightDrawParaLine(tx *textObject, offset, extraspace float64, text string, words int, last bool) float64 {
	simple := extraspace > -1e-8
	nSpaces := 0
	if !simple {
		nSpaces = words + nbspCount(text) - 1
		simple = nSpaces <= 0
	}
	var m float64
	if simple {
		m = offset + extraspace
		tx.setXPos(m)
		tx.textOut(text, true)
	} else {
		m = offset
		tx.setWordSpace(extraspace / float64(nSpaces))
		tx.setXPos(m)
		tx.textOut(text, true)
		tx.setWordSpace(0)
	}
	tx.setXPos(-m)
	return m
}

// justifyDrawParaLine is _justifyDrawParaLine.
func justifyDrawParaLine(tx *textObject, offset, extraspace float64, text string, words int, last bool) float64 {
	tx.setXPos(offset)
	simple := (-1e-8 < extraspace && extraspace <= 1e-8) || (last && extraspace > -1e-8)
	nSpaces := 0
	if !simple {
		nSpaces = words + nbspCount(text) - 1
		simple = nSpaces <= 0
	}
	if simple {
		tx.textOut(text, true)
	} else {
		tx.setWordSpace(extraspace / float64(nSpaces))
		tx.textOut(text, true)
		tx.setWordSpace(0)
	}
	tx.setXPos(-offset)
	return offset
}

// justifyDrawParaLineRTL is _justifyDrawParaLineRTL: a last line is right
// aligned, the others justified.
func justifyDrawParaLineRTL(tx *textObject, offset, extraspace float64, text string, words int, last bool) float64 {
	if last {
		return rightDrawParaLine(tx, offset, extraspace, text, words, last)
	}
	return justifyDrawParaLine(tx, offset, extraspace, text, words, last)
}

// drawParaLine is the _xxxDrawParaLine of the alignment.
func (p *paragraph) drawParaLine(tx *textObject, offset, extraspace float64, line simpleLine, last bool) float64 {
	text := simpleText(line.words)
	switch p.style.alignment() {
	case alignCenter:
		return centerDrawParaLine(tx, offset, extraspace, text, len(line.words), last)
	case alignRight:
		return rightDrawParaLine(tx, offset, extraspace, text, len(line.words), last)
	case alignJustify:
		if p.style.wordWrap == "RTL" {
			return justifyDrawParaLineRTL(tx, offset, extraspace, text, len(line.words), last)
		}
		return justifyDrawParaLine(tx, offset, extraspace, text, len(line.words), last)
	}
	return leftDrawParaLine(tx, offset, extraspace, text, len(line.words), last)
}

// usVals is the L, F and f values of the one-font layout.
func usVals(size float64) map[byte]float64 {
	return map[byte]float64{'L': size, 'F': size, 'f': size}
}

// doUnderLine is _do_under_line of the one-font layout.
func (p *paragraph) doUnderLine(c *canvas, curY float64, i int, x1, ws float64, f *frag, line simpleLine, defaultWidth float64) {
	size := f.size.float()
	y0 := curY - float64(i)*p.style.leadingValue()
	text := simpleText(line.words)
	x2 := x1 + stringWidth(text, f.font, size) + ws
	for _, l := range f.us {
		drawUS(c, l, x1, x2, y0, defaultWidth, usVals(size), f.color)
	}
}

// doLinkLine is _do_link_line of the one-font layout.
func (p *paragraph) doLinkLine(c *canvas, curY float64, i int, tOff float64, f *frag, line simpleLine) {
	leading := p.style.leadingValue()
	size := f.size.float()
	y := curY - float64(i)*leading - size/8.0
	width := stringWidth(simpleText(line.words), f.font, size)
	for _, l := range f.links {
		doLink(c, l.target, [4]float64{tOff, y, tOff + width, y + leading})
	}
}

// leftDrawParaLineX is _leftDrawParaLineX.
func (p *paragraph) leftDrawParaLineX(tx *textObject, xs *fragState, offset float64, line *fragLine, last bool) {
	tx.xOffset = offset
	tx.setXPos(offset)
	extraSpace := line.extra
	simple := extraSpace > -1e-8
	nSpaces := 0
	if !simple {
		nSpaces = line.wordCount + lineNBSP(line) - 1
		simple = nSpaces <= 0
	}
	if simple {
		p.putFragLine(tx, xs, offset, line, last, "left")
	} else {
		tx.setWordSpace(extraSpace / float64(nSpaces))
		p.putFragLine(tx, xs, offset, line, last, "left")
		tx.setWordSpace(0)
	}
	tx.setXPos(-offset)
}

// centerDrawParaLineX is _centerDrawParaLineX.
func (p *paragraph) centerDrawParaLineX(tx *textObject, xs *fragState, offset float64, line *fragLine, last bool) {
	tx.xOffset = offset
	dots := offset + tx.x0
	tx.dotsOffsetX = &dots
	defer func() { tx.dotsOffsetX = nil }()
	extraSpace := line.extra
	simple := extraSpace > -1e-8
	nSpaces := 0
	if !simple {
		nSpaces = line.wordCount + lineNBSP(line) - 1
		simple = nSpaces <= 0
	}
	var m float64
	if simple {
		m = offset + 0.5*line.extra
		tx.setXPos(m)
		p.putFragLine(tx, xs, m, line, last, "center")
	} else {
		m = offset
		tx.setWordSpace(extraSpace / float64(nSpaces))
		p.putFragLine(tx, xs, m, line, last, "center")
		tx.setWordSpace(0)
	}
	tx.setXPos(-m)
}

// rightDrawParaLineX is _rightDrawParaLineX.
func (p *paragraph) rightDrawParaLineX(tx *textObject, xs *fragState, offset float64, line *fragLine, last bool) {
	tx.xOffset = offset
	extraSpace := line.extra
	simple := extraSpace > -1e-8
	nSpaces := 0
	if !simple {
		nSpaces = line.wordCount + lineNBSP(line) - 1
		simple = nSpaces <= 0
	}
	var m float64
	if simple {
		m = offset + line.extra
		tx.setXPos(m)
		p.putFragLine(tx, xs, m, line, last, "right")
	} else {
		m = offset
		tx.setWordSpace(extraSpace / float64(nSpaces))
		p.putFragLine(tx, xs, m, line, last, "right")
		tx.setWordSpace(0)
	}
	tx.setXPos(-m)
}

// justifyDrawParaLineX is _justifyDrawParaLineX.
func (p *paragraph) justifyDrawParaLineX(tx *textObject, xs *fragState, offset float64, line *fragLine, last bool) {
	tx.xOffset = offset
	tx.setXPos(offset)
	extraSpace := line.extra
	simple := line.lineBreak || (-1e-8 < extraSpace && extraSpace <= 1e-8) || (last && extraSpace > -1e-8)
	nSpaces := 0
	if !simple {
		nSpaces = line.wordCount + lineNBSP(line) - 1
		simple = nSpaces <= 0
	}
	if !simple {
		tx.setWordSpace(extraSpace / float64(nSpaces))
		p.putFragLine(tx, xs, offset, line, last, "justify")
		tx.setWordSpace(0)
	} else {
		p.putFragLine(tx, xs, offset, line, last, "justify")
	}
	tx.setXPos(-offset)
}

// justifyDrawParaLineXRTL is _justifyDrawParaLineXRTL: a last line is right
// aligned, the others justified.
func (p *paragraph) justifyDrawParaLineXRTL(tx *textObject, xs *fragState, offset float64, line *fragLine, last bool) {
	if last {
		p.rightDrawParaLineX(tx, xs, offset, line, last)
		return
	}
	p.justifyDrawParaLineX(tx, xs, offset, line, last)
}

// drawParaLineX is the _xxxDrawParaLineX of the alignment.
func (p *paragraph) drawParaLineX(tx *textObject, xs *fragState, offset float64, line *fragLine, last bool) {
	switch p.style.alignment() {
	case alignCenter:
		p.centerDrawParaLineX(tx, xs, offset, line, last)
	case alignRight:
		p.rightDrawParaLineX(tx, xs, offset, line, last)
	case alignJustify:
		if p.style.wordWrap == "RTL" {
			p.justifyDrawParaLineXRTL(tx, xs, offset, line, last)
			return
		}
		p.justifyDrawParaLineX(tx, xs, offset, line, last)
	default:
		p.leftDrawParaLineX(tx, xs, offset, line, last)
	}
}

// draw is Paragraph.drawPara.
func (p *paragraph) draw(c *canvas) {
	style := p.style
	bw := style.borderWidth
	bc := style.borderColor
	bg := style.backColor
	// if bg or (bc and bw): the truth of the colour values and of the width.
	if (bg != nil && colorTruthy(*bg)) || (bc != nil && colorTruthy(*bc) && bw.truthy()) {
		c.saveState()
		stroke, fill := false, false
		if bc != nil && colorTruthy(*bc) && bw.truthy() {
			c.setStrokeColor(*bc)
			c.setLineWidth(bw.float())
			stroke = true
		}
		if bg != nil && colorTruthy(*bg) {
			c.setFillColor(*bg)
			fill = true
		}
		bp := style.borderPadding
		x := numSubOrErr(style.leftIndent, bp)
		y := numSubOrErr(pyNum{}, bp)
		indents := numAddOrErr(style.leftIndent, style.rightIndent)
		w := numAddOrErr(numAddOrErr(numSubOrErr(numFloat(p.width), indents), bp), bp)
		h := numAddOrErr(numAddOrErr(p.heightNum, bp), bp)
		if style.borderRadius.truthy() {
			c.roundRect(x.float(), y.float(), w.float(), h.float(), style.borderRadius.float(), stroke, fill)
		} else {
			c.rect(x.float(), y.float(), w.float(), h.float(), stroke, fill)
		}
		c.restoreState()
	}
	nLines := p.lineCount()
	if nLines == 0 {
		return
	}
	leftIndent := style.leftIndent.float()
	curX := leftIndent
	offsets := p.offsetsOf(nLines)
	c.saveState()
	alignment := style.alignment()
	offset := numAddOrErr(style.firstLineIndent, numFloat(offsets[0])).float()
	lim := nLines - 1
	noJustifyLast := !p.justifyLast
	jllwc := style.justifyLastLine
	defaultWidth := c.state.lineWidth
	isRTL := style.wordWrap == "RTL"
	var tx *textObject
	if isRTL && p.kind == 1 {
		// A right-to-left paragraph draws the words of each line reversed.
		for _, line := range p.fragLines {
			for i, j := 0, len(line.words)-1; i < j; i, j = i+1, j-1 {
				line.words[i], line.words[j] = line.words[j], line.words[i]
			}
		}
	}
	if p.kind == 0 {
		f := p.lineFrag
		size := f.size.float()
		curY := numSubOrErr(p.heightNum, f.size)
		if p.bullet != nil {
			offset = p.drawBullet(c, offset, curY.float(), isRTL)
		}
		c.setFillColor(f.color)
		tx = &textObject{c: c}
		leading := style.leading
		switch style.autoLeading {
		case "max":
			leading = numMax(leading, numSubOrErr(p.ascent, p.descent))
		case "min":
			leading = numSubOrErr(p.ascent, p.descent)
		}
		tx.font, tx.fontSize, tx.leading, tx.hasLeading = f.font, size, leading.float(), true
		tx.code = []string{"BT"}
		tx.setTextOrigin(curX, curY.float())
		tx.code = append(tx.code, c.fontRef(f.font)+" "+fpStr(size)+" Tf "+fpStr(tx.leading)+" TL")
		line := p.lines[0]
		lastLine := noJustifyLast && nLines == 1
		if lastLine && jllwc.truthy() && numLess(jllwc, numInt(int64(len(line.words)))) {
			lastLine = false
		}
		tOff := p.drawParaLine(tx, offset, line.extra, line, lastLine)
		if len(f.us) > 0 || len(f.links) > 0 {
			ws := line.extra
			if alignment != alignJustify || lastLine {
				ws = 0
			}
			dx := tOff + leftIndent
			if len(f.us) > 0 {
				p.doUnderLine(c, curY.float(), 0, dx, ws, f, line, defaultWidth)
			}
			if len(f.links) > 0 {
				p.doLinkLine(c, curY.float(), 0, dx, f, line)
			}
			for i := 1; i < nLines; i++ {
				line := p.lines[i]
				lastLine := noJustifyLast && i == lim
				if lastLine && jllwc.truthy() && numLess(jllwc, numInt(int64(len(line.words)))) {
					lastLine = false
				}
				tOff := p.drawParaLine(tx, offsets[i], line.extra, line, lastLine)
				dx := tOff + leftIndent
				ws := line.extra
				if alignment != alignJustify || lastLine {
					ws = 0
				}
				if len(f.us) > 0 {
					p.doUnderLine(c, curY.float(), i, tOff, ws, f, line, defaultWidth)
				}
				if len(f.links) > 0 {
					p.doLinkLine(c, curY.float(), i, dx, f, line)
				}
			}
		} else {
			for i := 1; i < nLines; i++ {
				line := p.lines[i]
				lastLine := noJustifyLast && i == lim
				if lastLine && jllwc.truthy() && numLess(jllwc, numInt(int64(len(line.words)))) {
					lastLine = false
				}
				p.drawParaLine(tx, offsets[i], line.extra, line, lastLine)
			}
		}
	} else {
		first := p.fragLines[0]
		curY := numSubOrErr(p.heightNum, first.fontSize)
		if p.bullet != nil {
			offset = p.drawBullet(c, offset, curY.float(), isRTL)
		}
		tx = &textObject{c: c}
		tx.code = []string{"BT"}
		tx.setTextOrigin(curX, curY.float())
		xs := &fragState{us: map[string]*usState{}, links: map[linkRef]*linkState{}, first: first, defaultWidth: defaultWidth,
			leading: style.leadingValue(), leftIndent: leftIndent, autoLeading: style.autoLeading, paraWidth: p.width, style: style,
			curY: curY}
		line := p.fragLines[0]
		lastLine := noJustifyLast && nLines == 1
		if lastLine && jllwc.truthy() && numLess(jllwc, numInt(int64(line.wordCount))) {
			lastLine = false
		}
		p.drawParaLineX(tx, xs, offset, line, lastLine)
		p.postText(tx, xs)
		for i := 1; i < nLines; i++ {
			line := p.fragLines[i]
			lastLine := noJustifyLast && i == lim
			if lastLine && jllwc.truthy() && numLess(jllwc, numInt(int64(line.wordCount))) {
				lastLine = false
			}
			p.drawParaLineX(tx, xs, offsets[i], line, lastLine)
			p.postText(tx, xs)
		}
	}
	c.add(strings.Join(append(tx.code, "ET"), " "))
	c.restoreState()
}
