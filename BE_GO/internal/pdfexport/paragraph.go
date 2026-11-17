package pdfexport

import (
	"math"
	"strings"
)

// paraStyle is the part of ParagraphStyle the documents set.
type paraStyle struct {
	fontName    string
	fontSize    pyNum
	leading     pyNum
	centred     bool
	spaceBefore pyNum
	spaceAfter  pyNum
	textColor   color
	align       int
	backColor   *color
	// shrink is spaceShrinkage when shrinkSet; denyWidows, allowOrphans and
	// noSplit are allowWidows=0, allowOrphans=1 and splitLongWords=0.
	shrink       pyNum
	shrinkSet    bool
	denyWidows   bool
	allowOrphans bool
	noSplit      bool
	// lines are the underline and strike defaults of the style; linesSet is
	// false for a style built by a document, which has reportlab's.
	lines    [2]lineDefaults
	linesSet bool
	// leftIndent, rightIndent and firstLineIndent are the indents of the
	// style.
	leftIndent, rightIndent, firstLineIndent pyNum
	// autoLeading is 'min', 'max', 'off' or '' (the style's value).
	autoLeading string
	// textTransform is 'uppercase', 'lowercase', 'capitalize', 'none' or ''
	// (the style's value).
	textTransform string
	// endDots is the text of the style's endDots ('' when it has none).
	endDots         string
	justifyBreaks   bool
	justifyLastLine pyNum
	// borderWidth, borderPadding, borderColor and borderRadius are the
	// border attributes; borderRadius 0 is the style's None (a square corner
	// and the 0 a radius draws).
	borderWidth, borderPadding pyNum
	borderColor                *color
	borderRadius               pyNum
	// borderRadiusSet is false while borderRadius is the style's None: a
	// relative value added to it raises, as Python's None + number does.
	borderRadiusSet bool
	// bulletFontName, bulletFontSize, bulletIndent, bulletAnchor,
	// bulletOffsetY and bulletColor are the bullet attributes; bulletColor is
	// nil unless the markup sets one.
	bulletFontName string
	bulletFontSize pyNum
	bulletIndent   pyNum
	bulletAnchor   string
	bulletOffsetY  pyNum
	bulletColor    *color
}

// lineDefaults are underlineWidth, underlineOffset, underlineGap and
// underlineColor of ParagraphStyle (and the strike ones).
type lineDefaults struct {
	hasColor           bool
	color              color
	width, offset, gap usVal
}

const (
	alignLeft = iota
	alignCenter
	alignRight
)

// alignJustify is TA_JUSTIFY, the alignment reportlab numbers 4.
const alignJustify = 4

// alignment is the alignment of the style: centred for the documents'
// centred styles, else align.
func (s paraStyle) alignment() int {
	if s.centred {
		return alignCenter
	}
	return s.align
}

// withDefaults fills the ParagraphStyle defaults a style literal of this
// package leaves zero: the bullet font, its size and its anchor.
func (s paraStyle) withDefaults() paraStyle {
	if s.bulletFontName == "" {
		s.bulletFontName = "Helvetica"
	}
	if !s.bulletFontSize.truthy() {
		s.bulletFontSize = numInt(10)
	}
	if s.bulletAnchor == "" {
		s.bulletAnchor = "start"
	}
	return s
}

// shrinkage is spaceShrinkage, as the layout's number.
func (s paraStyle) shrinkage() pyNum {
	if s.shrinkSet {
		return s.shrink
	}
	return numFloat(0.05)
}

// shrinkValue is spaceShrinkage as reportlab multiplies by it.
func (s paraStyle) shrinkValue() float64 { return s.shrinkage().float() }

// leadingValue is the style's leading as the layout uses it.
func (s paraStyle) leadingValue() float64 { return s.leading.float() }

func defaultLines() [2]lineDefaults {
	return [2]lineDefaults{
		lineUnderline: {width: usVal{}, offset: usVal{str: "-0.125*F"}, gap: usVal{str: "1"}},
		lineStrike:    {width: usVal{}, offset: usVal{str: "0.25*F"}, gap: usVal{str: "1"}},
	}
}

// styleNormal is getSampleStyleSheet()['Normal'].
func styleNormal() paraStyle {
	return paraStyle{fontName: "Helvetica", fontSize: numInt(10), leading: numInt(12)}
}

// paraFailure is an exception reportlab raises while it lays a paragraph
// out; docTemplate.build answers it as ErrLayout.
type paraFailure struct{ reason string }

const (
	softHyphen = '­'
	fsNever    = float64(0x7fffffff)
)

// numFail is a pyNum operation that failed: reportlab's OverflowError for an
// int beyond the range of float64.
func numFail[T any](value T, ok bool) T {
	if !ok {
		panic(paraFailure{"int too large to convert to float"})
	}
	return value
}

// numAddOrErr is a+b for an int too large for a float.
func numAddOrErr(a, b pyNum) pyNum { return numFail(numAdd(a, b)) }

// numSubOrErr is a-b for an int too large for a float.
func numSubOrErr(a, b pyNum) pyNum { return numFail(numSub(a, b)) }

// numMulOrErr is a*b for an int too large for a float.
func numMulOrErr(a, b pyNum) pyNum { return numFail(numMul(a, b)) }

// pyNumberType is type(v).__name__ of a converted attribute value.
func pyNumberType(n pyNum) string {
	if n.isInt() {
		return "int"
	}
	return "float"
}

// maxFloatNum is Python's max(f, n), a float against a number: the float stays
// unless n compares greater, so a NaN float is the result.
func maxFloatNum(f float64, n pyNum) pyNum {
	if numLess(numFloat(f), n) {
		return n
	}
	return numFloat(f)
}

// simpleWord is a word of a one-font paragraph: a str, a _SplitWord piece of
// a long word (the last one a _SplitWordEnd) or a _SHYStr with the places of
// its soft hyphens.
type simpleWord struct {
	text  []rune
	split bool
	end   bool
	shy   bool
	sp    []int
	fsww  float64
}

func plainWords(texts []string) []*simpleWord {
	words := make([]*simpleWord, len(texts))
	for i, text := range texts {
		words[i] = &simpleWord{text: []rune(text)}
	}
	return words
}

// newSHYStr is _SHYStr(s).
func newSHYStr(s []rune) *simpleWord {
	word := &simpleWord{shy: true}
	var ends []int
	for _, r := range s {
		if r == softHyphen {
			ends = append(ends, len(word.text))
			continue
		}
		word.text = append(word.text, r)
	}
	word.sp = ends
	return word
}

// shySplit is _SHYStr.__shysplit__: the longest head ending at a soft hyphen
// that fits with its '-', and the rest.
func (w *simpleWord) shySplit(f *font, size, baseWidth, limWidth float64) []*simpleWord {
	w.fsww = fsNever
	for i := len(w.sp) - 1; i >= 0; i-- {
		sp := w.sp[i]
		head := w.text[:sp]
		width := stringWidth(string(head), f, size)
		if i == 0 {
			w.fsww = width
		}
		if baseWidth+width <= limWidth {
			first := &simpleWord{text: append(append([]rune{}, head...), '-'), shy: true, sp: append([]int{}, w.sp[:i]...)}
			var rest []rune
			bounds := append(append([]int{}, w.sp[i:]...), len(w.text))
			for j := 0; j+1 < len(bounds); j++ {
				if j > 0 {
					rest = append(rest, softHyphen)
				}
				rest = append(rest, w.text[bounds[j]:bounds[j+1]]...)
			}
			return []*simpleWord{first, newSHYStr(rest)}
		}
	}
	return nil
}

type simpleLine struct {
	extra float64
	words []*simpleWord
}

// shyText is a str of a frag word, or a _SHYIndexedStr with the places of
// its soft hyphens.
type shyText struct {
	s       []rune
	indexed bool
	idx     []int
}

// newSHYIndexed is _SHYIndexedStr(u, X).
func newSHYIndexed(u []rune, x []int) shyText {
	if len(x) > 0 {
		return shyText{s: u, indexed: true, idx: x}
	}
	out := shyText{indexed: true}
	var ends []int
	for _, r := range u {
		if r == softHyphen {
			ends = append(ends, len(out.s))
			continue
		}
		out.s = append(out.s, r)
	}
	out.idx = ends
	return out
}

// shyUnsplit is _shyUnsplit, with the exceptions reportlab raises.
func shyUnsplit(s shyText, ss *shyText) shyText {
	u := append([]rune{}, s.s...)
	for len(u) > 0 && u[len(u)-1] == '-' {
		u = u[:len(u)-1]
	}
	if s.indexed {
		if len(s.idx) == 0 {
			panic(paraFailure{"list index out of range"})
		}
		x := append([]int{}, s.idx...)
		last := x[len(x)-1]
		if ss != nil && len(ss.s) > 0 {
			if ss.indexed {
				for _, v := range ss.idx {
					x = append(x, v+last)
				}
			}
			u = append(u, ss.s...)
		}
		return newSHYIndexed(u, x)
	}
	if ss != nil && len(ss.s) > 0 {
		u = append(u, ss.s...)
		if ss.indexed {
			panic(paraFailure{"X is not defined"})
		}
	}
	return shyText{s: u}
}

type fragPart struct {
	f    *frag
	text shyText
}

// fragWord is a frag word of _getFragWords: its width and its parts, with
// the classes of paragraph.py as flags (_HSFrag, _InjectedFrag, _SplitFrag,
// _SplitFragH, _SplitFragHY, _SHYWord).
type fragWord struct {
	width                                     float64
	parts                                     []fragPart
	hs, injected, split, splitH, splitHY, shy bool
	fsww                                      float64
}

type fragLine struct {
	extra     float64
	wordCount int
	words     []*frag
	fontSize  pyNum
	sFW       int
	// ascent, descent and maxWidth are the bounds of breakLines and the width
	// the line was broken to (the last two are the endDots ones).
	ascent, descent, maxWidth float64
	// lineBreak is FragLine.lineBreak: the line ended at a <br/> that
	// justifyBreaks leaves unjustified.
	lineBreak bool
}

// paraBullet is Paragraph.bulletText: a str, or the fragments a <bullet> tag
// makes (one fragment per run of markup text).
type paraBullet struct {
	text  string
	frags []*frag
}

// isText is Python's truth of the bulletText: a str, or a non-empty list.
func (b *paraBullet) isText() bool { return b.frags == nil }

// paragraph is platypus.Paragraph: the fragments of the parser, laid out as
// lines of one font (kind 0) or of several fragments (kind 1).
type paragraph struct {
	style     paraStyle
	frags     []*frag
	bullet    *paraBullet
	processed []*fragWord // self.frags once it is a frag word list
	isWords   bool
	wrapped   bool
	kind      int
	lines     []simpleLine
	fragLines []*fragLine
	lineFrag  *frag
	width     float64
	height    float64
	// heightNum is self.height as reportlab keeps it: the line count times the
	// leading, exact while both are ints.
	heightNum pyNum
	// ascent and descent are blPara.ascent and blPara.descent of the
	// one-font layout.
	ascent, descent pyNum
	// wrapWidths are the first and the later line widths of wrap.
	wrapWidths [2]float64
	// justifyLast is _JustifyLast: the last line of a part a split made is
	// justified (the paragraph goes on).
	justifyLast bool
	// splitPara is _splitpara.
	splitPara bool
	// offsets is _offsets: the x offset of each line (none for these
	// documents).
	offsets []float64
}

// Python's str.isspace() characters and reportlab's _wsc (which has U+200B
// and leaves the no-break space alone).
const (
	pySpaces    = "\t\n\v\f\r\x1c\x1d\x1e\x1f \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
	rlSpaces    = "\t\n\v\f\r\x1c\x1d\x1e\x1f \u0085\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u200b\u2028\u2029\u202f\u205f\u3000"
	asciiSpaces = " \t\n\r\v\f"
)

// paraSplit is paragraph.split(text): str.split(), or a split on reportlab's
// own whitespace when the text has a no-break space (re.split keeps the empty
// strings at the ends).
func paraSplit(text string) []string {
	if !strings.ContainsRune(text, '\u00a0') {
		return strings.FieldsFunc(text, func(r rune) bool { return strings.ContainsRune(pySpaces, r) })
	}
	var out []string
	start, inSpace := 0, false
	for i, r := range text {
		space := strings.ContainsRune(rlSpaces, r)
		switch {
		case space && !inSpace:
			out = append(out, text[start:i])
			inSpace = true
		case !space && inSpace:
			start, inSpace = i, false
		}
	}
	if inSpace {
		return append(out, "")
	}
	return append(out, text[start:])
}

// paraClean is cleanBlockQuotedText: every line stripped, its words joined
// by one space, the non-empty lines joined by one space.
func paraClean(text string) string {
	var lines []string
	for _, line := range strings.Split(text, "\n") {
		var words []string
		for _, word := range paraSplit(strings.Trim(line, rlSpaces)) {
			if word != "" {
				words = append(words, word)
			}
		}
		if len(words) > 0 {
			lines = append(lines, strings.Join(words, " "))
		}
	}
	return strings.Join(lines, " ")
}

// newParagraph is Paragraph(text, style): the text cleaned, parsed and case
// transformed.
func newParagraph(text string, style paraStyle) (*paragraph, error) {
	style = style.withDefaults()
	frags, bullet, style, err := parseParagraph(paraClean(text), style)
	if err != nil {
		return nil, err
	}
	return &paragraph{style: style, frags: frags, bullet: bullet}, nil
}

func (p *paragraph) spaceBefore() float64 { return p.style.spaceBefore.float() }
func (p *paragraph) spaceAfter() float64  { return p.style.spaceAfter.float() }
func (p *paragraph) hAlign() string       { return "LEFT" }

func (p *paragraph) count() int {
	if p.processed != nil {
		return len(p.processed)
	}
	return len(p.frags)
}

// metrics is getAscentDescent(fontName, fontSize).
func metrics(f *font, size float64) (ascent, descent float64) {
	norm := size / 1000
	return float64(f.ascent) * norm, float64(f.descent) * norm
}

// splitWord is paragraph._splitWord.
func splitWord(word []rune, lineWidth float64, maxWidths []float64, lineno int, f *font, size float64) []*simpleWord {
	var out []*simpleWord
	var current []rune
	maxlineno := len(maxWidths) - 1
	maxWidth := maxWidths[min(maxlineno, lineno)]
	for _, r := range word {
		cw := stringWidth(string(r), f, size)
		newWidth := lineWidth + cw
		if newWidth > maxWidth {
			out = append(out, &simpleWord{text: current, split: true})
			lineno++
			maxWidth = maxWidths[min(maxlineno, lineno)]
			newWidth = cw
			current = nil
		}
		current = append(current, r)
		lineWidth = newWidth
	}
	return append(out, &simpleWord{text: current, split: true, end: true})
}

func hasSoftHyphen(text []rune) bool {
	for _, r := range text {
		if r == softHyphen {
			return true
		}
	}
	return false
}

// breakSimple is Paragraph.breakLines for one fragment: the line widths, the
// words of each line, and the ascent and descent of the font.
func (p *paragraph) breakSimple(maxWidths []float64) {
	f := p.frags[0]
	p.kind, p.lines, p.lineFrag = 0, nil, f
	ascent, descent := metrics(f.font, f.size.float())
	p.ascent, p.descent = numFloat(ascent), numFloat(descent)
	var words []*simpleWord
	if f.hasWords {
		blank := true
		for _, word := range f.words {
			if strings.Trim(string(word.text), rlSpaces) != "" {
				blank = false
				break
			}
		}
		if blank {
			return
		}
		words = append(words, f.words...)
	} else {
		text := strings.Trim(f.text, rlSpaces)
		if text == "" {
			return
		}
		words = plainWords(paraSplit(text))
	}
	size := f.size.float()
	spaceWidth := stringWidth(" ", f.font, size)
	dSpaceShrink := p.style.shrinkValue() * spaceWidth
	hyphenWidth := stringWidth("-", f.font, size)
	maxlineno := len(maxWidths) - 1
	lineno := 0
	maxWidth := maxWidths[0]
	var line []*simpleWord
	currentWidth := -spaceWidth
	forced := false
	for len(words) > 0 {
		word := words[0]
		words = words[1:]
		if len(word.text) == 0 && word.split {
			forced = true
		} else if hasSoftHyphen(word.text) {
			word = newSHYStr(word.text)
		}
		wordWidth := stringWidth(string(word.text), f.font, size)
		newWidth := currentWidth + spaceWidth + wordWidth
		limWidth := maxWidth + dSpaceShrink*float64(len(line))
		dropped := false
		if newWidth > limWidth && !forced {
			if word.shy {
				if parts := word.shySplit(f.font, size, currentWidth+spaceWidth+hyphenWidth-1e-8, limWidth); parts != nil {
					words = append(parts, words...)
					forced = true
					continue
				} else if len(line) > 0 {
					nMW := maxWidths[min(maxlineno, lineno)]
					if word.fsww+hyphenWidth+1e-8 <= nMW {
						if word.shySplit(f.font, size, hyphenWidth-1e-8, nMW) != nil {
							words = append([]*simpleWord{word}, words...)
							forced, dropped = true, true
							newWidth = currentWidth
						}
					}
				}
			}
			if !p.style.noSplit && !word.split && !forced && wordWidth > maxWidth {
				words = append(splitWord(word.text, currentWidth+spaceWidth, maxWidths, lineno, f.font, size), words...)
				forced = true
				continue
			}
		}
		if newWidth <= limWidth || len(line) == 0 || forced {
			if !dropped && len(word.text) > 0 {
				line = append(line, word)
			}
			if forced {
				forced = false
				p.lines = append(p.lines, simpleLine{maxWidth - newWidth, line})
				line = nil
				currentWidth = -spaceWidth
				lineno++
				maxWidth = maxWidths[min(maxlineno, lineno)]
			} else {
				currentWidth = newWidth
			}
		} else {
			p.lines = append(p.lines, simpleLine{maxWidth - currentWidth, line})
			line = []*simpleWord{word}
			currentWidth = wordWidth
			lineno++
			maxWidth = maxWidths[min(maxlineno, lineno)]
		}
	}
	if len(line) > 0 {
		p.lines = append(p.lines, simpleLine{maxWidth - currentWidth, line})
	}
}

func fragWidth(text []rune, f *frag) float64 {
	return stringWidth(string(text), f.font, f.size.float())
}

// rejoinSplitFragWords is _rejoinSplitFragWords.
func rejoinSplitFragWords(pieces []*fragWord) *fragWord {
	out := &fragWord{}
	length := 0.0
	var previous *frag
	for _, piece := range pieces {
		length += piece.width
		for i, part := range piece.parts {
			text := part.text
			if piece.splitHY && i == len(piece.parts)-1 {
				if len(text.s) == 0 {
					panic(paraFailure{"string index out of range"})
				}
				length -= fragWidth(text.s[len(text.s)-1:], part.f) + 1e-8
				text = shyUnsplit(text, nil)
			}
			if previous == part.f {
				last := &out.parts[len(out.parts)-1]
				last.text = shyUnsplit(last.text, &text)
			} else {
				out.parts = append(out.parts, fragPart{part.f, text})
				previous = part.f
			}
		}
	}
	out.width = length
	last := pieces[len(pieces)-1]
	switch {
	case last.shy && last.hs:
		out.shy, out.split, out.hs = true, true, true
	case last.shy:
		out.shy = true
	case last.hs:
		out.hs = true
	}
	return out
}

// fragWords is _getFragWords.
func (p *paragraph) fragWords(maxWidth float64, hasMax bool) []*fragWord {
	var out []*fragWord
	if p.processed != nil {
		var pieces []*fragWord
		for _, word := range p.processed {
			if word.injected {
				continue
			}
			if word.split {
				pieces = append(pieces, word)
				if word.hs {
					out = append(out, rejoinSplitFragWords(pieces))
					pieces = nil
				}
				continue
			}
			if len(pieces) > 0 {
				out = append(out, rejoinSplitFragWords(pieces))
				pieces = nil
			}
			out = append(out, word)
		}
		if len(pieces) > 0 {
			out = append(out, rejoinSplitFragWords(pieces))
		}
		if len(out) == 0 {
			panic(paraFailure{"no frag words"})
		}
		return out
	}
	word := &fragWord{}
	n := 0.0
	hangingStrip, shy := true, false
	add := func(f *frag, text string) {
		part := shyText{s: []rune(text)}
		if hasSoftHyphen(part.s) {
			part = newSHYIndexed(part.s, nil)
			shy = true
		}
		word.parts = append(word.parts, fragPart{f, part})
		n += fragWidth(part.s, f)
	}
	flush := func(set func(w *fragWord)) {
		word.width = n
		set(word)
		out = append(out, word)
		word, n, shy = &fragWord{}, 0, false
	}
	for _, f := range p.frags {
		text := f.text
		switch {
		case text != "":
			f.kind = fragText
			if hangingStrip {
				text = strings.TrimLeft(text, rlSpaces)
				if text == "" {
					continue
				}
				hangingStrip = false
			}
			pieces := paraSplit(text)
			if strings.ContainsRune(asciiSpaces, []rune(text)[0]) || len(pieces) == 0 {
				followed := false
				if len(word.parts) > 0 {
					wasShy := shy
					flush(func(w *fragWord) { w.shy = wasShy })
				} else {
					followed = len(out) > 0 && out[len(out)-1].hs
				}
				if !followed {
					pieces = append([]string{""}, pieces...)
				} else if len(pieces) == 0 {
					continue
				}
			}
			for _, piece := range pieces[:len(pieces)-1] {
				add(f, piece)
				wasShy := shy
				flush(func(w *fragWord) {
					if wasShy {
						w.shy, w.split = true, true
					}
					w.hs = true
				})
			}
			add(f, pieces[len(pieces)-1])
			runes := []rune(text)
			if strings.ContainsRune(asciiSpaces, runes[len(runes)-1]) {
				wasShy := shy
				flush(func(w *fragWord) {
					if wasShy {
						w.shy = true
					} else {
						w.hs = true
					}
				})
			}
		case f.anchor != nil:
			// _getFragWords: a cbDefn without width is appended to the word.
			f.kind = fragAppend
			if len(word.parts) == 0 && len(out) > 0 && out[len(out)-1].hs {
				last := out[len(out)-1]
				last.parts = append(last.parts, fragPart{f, shyText{}})
			} else {
				word.parts = append(word.parts, fragPart{f, shyText{}})
			}
		case f.img != nil:
			width := f.img.width.norm(numFloat(maxWidth))
			if f.img.width.pct && !hasMax {
				panic(paraFailure{"unsupported operand type(s) for *: 'NoneType' and 'float'"})
			}
			if f.img.width.truthy() {
				if len(word.parts) > 0 {
					flush(func(*fragWord) {})
				}
				f.kind = fragImage
				out = append(out, &fragWord{width: width, parts: []fragPart{{f, shyText{}}}})
				hangingStrip = false
			} else {
				f.kind = fragAppend
				if len(word.parts) == 0 && len(out) > 0 && out[len(out)-1].hs {
					last := out[len(out)-1]
					last.parts = append(last.parts, fragPart{f, shyText{}})
				} else {
					word.parts = append(word.parts, fragPart{f, shyText{}})
				}
			}
		case f.lineBreak:
			if len(word.parts) > 0 {
				flush(func(*fragWord) {})
			}
			f.kind = fragBreak
			out = append(out, &fragWord{parts: []fragPart{{f, shyText{}}}})
			hangingStrip = true
		}
	}
	if len(word.parts) > 0 {
		wasShy := shy
		flush(func(w *fragWord) { w.shy = wasShy })
	}
	if len(out) == 0 && len(p.frags) > 0 {
		p.frags[0].kind = fragText
		out = []*fragWord{{parts: []fragPart{{p.frags[0], shyText{}}}}}
	}
	return out
}

// shyphenate is _SHYWord.shyphenate: the word cut at its last soft hyphen
// that fits, as a head ending in '-' and the rest.
func (w *fragWord) shyphenate(newWidth, maxWidth float64) []*fragWord {
	ww := w.width
	w.fsww = fsNever
	if ww == 0 {
		return nil
	}
	type cut struct {
		i, j, x     int
		leftWidth   float64
		left, right []rune
		hyphen      float64
	}
	var possible *cut
	exceeded := false
	baseWidth := newWidth - ww
	baseWidth0 := baseWidth
	first, hasFirst := 0.0, false
	for i, part := range w.parts {
		if part.text.indexed {
			hyphen := fragWidth([]rune{'-'}, part.f)
			bw := baseWidth + hyphen
			for j, x := range part.text.idx {
				left, right := part.text.s[:min(x, len(part.text.s))], part.text.s[min(x, len(part.text.s)):]
				leftWidth := bw + fragWidth(left, part.f)
				if !hasFirst {
					first, hasFirst = leftWidth, true
				}
				exceeded = leftWidth > maxWidth
				if exceeded {
					break
				}
				possible = &cut{i, j, x, leftWidth, left, right, hyphen}
			}
			baseWidth += fragWidth(part.text.s, part.f)
		} else {
			baseWidth += fragWidth(part.text.s, part.f)
			exceeded = baseWidth > maxWidth
		}
		if exceeded && hasFirst {
			break
		}
	}
	if hasFirst {
		w.fsww = first - baseWidth0
	}
	if possible == nil {
		return nil
	}
	part := w.parts[possible.i]
	indices := part.text.idx
	lefts := newSHYIndexed(append(append([]rune{}, possible.left...), '-'), append([]int{}, indices[:possible.j+1]...))
	head := &fragWord{width: possible.leftWidth - baseWidth0, shy: true, split: true, splitH: true, splitHY: true}
	head.parts = append(append([]fragPart{}, w.parts[:possible.i]...), fragPart{part.f, lefts})
	tail := &fragWord{width: ww - head.width + possible.hyphen, shy: true, split: true, hs: true}
	if len(possible.right) > 0 {
		var rest []int
		for _, v := range indices[possible.j+1:] {
			rest = append(rest, v-possible.x)
		}
		tail.parts = append(tail.parts, fragPart{part.f, newSHYIndexed(append([]rune{}, possible.right...), rest)})
	}
	tail.parts = append(tail.parts, w.parts[possible.i+1:]...)
	return []*fragWord{head, tail}
}

// splitFragWord is _splitFragWord: a frag word too long for a line cut
// character by character.
func splitFragWord(w *fragWord, maxWidth float64, maxWidths []float64, lineno int) []*fragWord {
	var out []*fragWord
	var parts []fragPart
	lineWidth, wordWidth := 0.0, 0.0
	var text []rune
	f := w.parts[0].f
	maxlineno := len(maxWidths) - 1
	step := func(g *frag, cw float64, c []rune) {
		newLineWidth := lineWidth + cw
		tooLong := newLineWidth > maxWidth
		if g != f || tooLong {
			f = f.clone()
			f.text = string(text)
			parts = append(parts, fragPart{f, shyText{s: text}})
			if tooLong {
				out = append(out, &fragWord{width: wordWidth, parts: parts, split: true})
				lineno++
				maxWidth = maxWidths[min(maxlineno, lineno)]
				parts = nil
				newLineWidth = cw
				wordWidth = 0
			}
			text = nil
			f = g
		}
		wordWidth += cw
		text = append(append([]rune{}, text...), c...)
		lineWidth = newLineWidth
	}
	for _, part := range w.parts {
		if len(part.text.s) == 0 {
			step(part.f, 0, nil)
			continue
		}
		for _, r := range part.text.s {
			step(part.f, fragWidth([]rune{r}, part.f), []rune{r})
		}
	}
	parts = append(parts, fragPart{f, shyText{s: text}})
	last := &fragWord{width: wordWidth, parts: parts, split: true}
	if w.hs {
		last.hs = true
	} else {
		last.splitH = true
	}
	return append(out, last)
}

// breakFrags is Paragraph.breakLines for several fragments or a frag word
// list.
func (p *paragraph) breakFrags(maxWidths []float64) {
	p.kind, p.fragLines = 1, nil
	maxlineno := len(maxWidths) - 1
	lineno := 0
	maxWidth := maxWidths[0]
	pending := p.fragWords(maxWidth, true)
	var words []*frag
	var done []*fragWord
	var g *frag
	sFW, n := 0, 0
	spaceWidth, currentWidth := 0.0, 0.0
	maxSize := pyNum{}
	maxAscent, minDescent := 0.0, 0.0
	calcBounds := p.style.autoLeading != "" && p.style.autoLeading != "off"
	injected := func(f *frag) *fragWord {
		broken := f.clone()
		broken.kind, broken.text = fragBreak, ""
		return &fragWord{injected: true, parts: []fragPart{{broken, shyText{}}}}
	}
	// bounds adds the ascent and descent of a fragment to the line's.
	bounds := func(f *frag, size float64) {
		var ascent, descent float64
		if f.kind == fragImage && f.img != nil {
			descent, ascent = imgVRange(f.img.height.norm(numFloat(size)), f.img.valign, numFloat(size))
		} else {
			ascent, descent = metrics(f.font, size)
		}
		maxAscent = pyMax(maxAscent, ascent)
		minDescent = pyMin(minDescent, descent)
	}
	// restart is the line a word too long to fit starts.
	restart := func(w *fragWord, f *frag, wordWidth float64) {
		spaceWidth = 0
		if w.hs {
			spaceWidth = stringWidth(" ", f.font, f.size.float())
		}
		currentWidth = wordWidth
		n = 1
		g = f.clone()
		maxSize = g.size
		if calcBounds {
			if g.kind == fragImage && g.img != nil {
				minDescent, maxAscent = imgVRange(g.img.height.norm(maxSize), g.img.valign, maxSize)
			} else {
				maxAscent, minDescent = metrics(g.font, maxSize.float())
			}
		} else {
			maxAscent, minDescent = metrics(g.font, maxSize.float())
		}
		words = []*frag{g}
		g.text = string(w.parts[0].text.s)
		for _, part := range w.parts[1:] {
			g = part.f.clone()
			g.text = string(part.text.s)
			words = append(words, g)
			maxSize = numMax(maxSize, g.size)
			bounds(g, g.size.float())
		}
	}
	for len(pending) > 0 {
		w := pending[0]
		pending = pending[1:]
		done = append(done, w)
		if len(w.parts) == 0 {
			panic(paraFailure{"'float' object is not subscriptable"})
		}
		last := w.parts[len(w.parts)-1].f
		lastFont, lastSize := last.font, last.size
		if len(words) == 0 {
			n, spaceWidth, currentWidth = 0, 0, 0
			maxSize = lastSize
			maxAscent, minDescent = metrics(lastFont, lastSize.float())
		}
		wordWidth := w.width
		f := w.parts[0].f
		newWidth := currentWidth
		if wordWidth > 0 {
			newWidth = currentWidth + spaceWidth + wordWidth
		}
		lineBreak := f.kind == fragBreak
		spaceShrink := spaceWidth
		for _, wi := range words {
			if wi.kind == fragText {
				if ns := strings.Count(wi.text, " "); ns > 0 {
					spaceShrink += float64(ns) * stringWidth(" ", wi.font, wi.size.float())
				}
			}
		}
		limWidth := maxWidth + spaceShrink*p.style.shrinkValue()
		if !lineBreak && newWidth > limWidth && !w.splitH && f.img == nil && f.anchor == nil {
			if w.shy {
				if cut := w.shyphenate(newWidth, limWidth); cut != nil {
					pending = append([]*fragWord{cut[0], injected(f), cut[1]}, pending...)
					done = done[:len(done)-1]
					continue
				} else if len(done) > 1 && w.fsww+1e-8 <= maxWidth {
					if w.shyphenate(wordWidth, maxWidth) != nil {
						pending = append([]*fragWord{injected(f), w}, pending...)
						done = done[:len(done)-1]
						continue
					}
				}
			}
			if !p.style.noSplit && !w.split && wordWidth > maxWidth {
				cut := splitFragWord(w, maxWidth-spaceWidth-currentWidth, maxWidths, lineno)
				pending = append(append([]*fragWord{cut[0], injected(f)}, cut[1:]...), pending...)
				done = done[:len(done)-1]
				continue
			}
		}
		// The space after a word is that of its last fragment's font, at the
		// size of the first fragment when the word stays on the line and of
		// the last one when it starts a line (paragraph.py reuses fontSize).
		follow := func(size float64) float64 {
			if w.hs {
				return stringWidth(" ", lastFont, size)
			}
			return 0
		}
		if !(newWidth > limWidth && n > 0) && !lineBreak {
			text := string(w.parts[0].text.s)
			if text != "" {
				n++
			}
			maxSize = numMax(maxSize, f.size)
			bounds(f, f.size.float())
			switch {
			case len(words) == 0:
				g = f.clone()
				words = []*frag{g}
				g.text = text
			case !sameFrag(g, f):
				if spaceWidth != 0 {
					for i := len(words) - 1; i >= 0; i-- {
						if words[i].kind == fragText {
							if !strings.HasSuffix(words[i].text, " ") {
								words[i].text += " "
							}
							break
						}
					}
				}
				g = f.clone()
				words = append(words, g)
				g.text = text
			case spaceWidth != 0:
				if !strings.HasSuffix(g.text, " ") {
					g.text += " " + text
				} else {
					g.text += text
				}
			default:
				g.text += text
			}
			spaceWidth = follow(f.size.float())
			more := false
			for _, part := range w.parts[1:] {
				g = part.f.clone()
				g.text = string(part.text.s)
				if g.text != "" {
					more = true
				}
				words = append(words, g)
				maxSize = numMax(maxSize, g.size)
				bounds(g, g.size.float())
			}
			if text == "" && more {
				n++
			}
			currentWidth = newWidth
			continue
		}
		llb := false
		if lineBreak {
			g = f.clone()
			words = append(words, g)
			llb = !p.style.justifyBreaks && !w.injected
		}
		p.fragLines = append(p.fragLines, &fragLine{extra: maxWidth - currentWidth, wordCount: n, words: words, fontSize: maxSize,
			sFW: sFW, ascent: maxAscent, descent: minDescent, maxWidth: maxWidth, lineBreak: llb})
		sFW = len(done) - 1
		lineno++
		maxWidth = maxWidths[min(maxlineno, lineno)]
		if lineBreak {
			words = nil
			continue
		}
		restart(w, f, wordWidth)
	}
	if len(words) > 0 {
		p.fragLines = append(p.fragLines, &fragLine{extra: maxWidth - currentWidth, wordCount: n, words: words, fontSize: maxSize,
			sFW: sFW, ascent: maxAscent, descent: minDescent, maxWidth: maxWidth})
	}
	p.processed = done
}

// simpleKind is the test of Paragraph.breakLines for the one-font layout: a
// single fragment that is no image or anchor, has no background and no
// endDots.
func (p *paragraph) simpleKind() bool {
	return len(p.frags) == 1 && p.frags[0].img == nil && p.frags[0].anchor == nil && p.frags[0].back == nil &&
		p.style.endDots == ""
}

func (p *paragraph) lineCount() int {
	if p.kind == 1 {
		return len(p.fragLines)
	}
	return len(p.lines)
}

// handleBulletWidth is _handleBulletWidth: a bullet wider than the first
// line's indent shortens the first line.
func (p *paragraph) handleBulletWidth(maxWidths []float64) {
	b := p.bullet
	if b == nil {
		return
	}
	style := p.style
	var bulletWidth float64
	if b.isText() {
		bulletWidth = stringWidth(b.text, bulletFont(style.bulletFontName), style.bulletFontSize.float())
	} else {
		for _, f := range b.frags {
			bulletWidth += stringWidth(f.text, f.font, f.size.float())
		}
	}
	bulletLen := numAddOrErr(numAddOrErr(style.bulletIndent, numFloat(bulletWidth)),
		numMulOrErr(numFloat(0.6), style.bulletFontSize))
	indent := numAddOrErr(style.leftIndent, style.firstLineIndent)
	if numLess(indent, bulletLen) {
		//..then it overruns, and we have less space available on line 1
		maxWidths[0] = numSubOrErr(numFloat(maxWidths[0]), numSubOrErr(bulletLen, indent)).float()
	}
}

func (p *paragraph) wrap(c *canvas, availWidth, availHeight float64) (float64, float64) {
	if availWidth < fuzz {
		return 0, 0x7fffffff
	}
	p.width = availWidth
	style := p.style
	leftIndent := style.leftIndent
	first := numSubOrErr(numSubOrErr(numFloat(availWidth), numAddOrErr(leftIndent, style.firstLineIndent)), style.rightIndent).float()
	later := numSubOrErr(numSubOrErr(numFloat(availWidth), leftIndent), style.rightIndent).float()
	p.wrapWidths = [2]float64{first, later}
	p.wrapped = true
	widths := []float64{first, later}
	p.handleBulletWidth(widths)
	switch {
	case p.count() == 0:
		p.kind, p.lines, p.fragLines, p.lineFrag = 0, nil, nil, &frag{font: fontsByName[style.fontName], size: style.fontSize,
			color: style.textColor}
		p.ascent, p.descent = style.fontSize, numMulOrErr(numFloat(-0.2), style.fontSize)
	case p.processed == nil && p.simpleKind():
		p.breakSimple(widths)
	default:
		p.breakFrags(widths)
	}
	autoLeading := style.autoLeading
	var height pyNum
	switch {
	case p.kind == 1:
		if autoLeading != "" && autoLeading != "off" {
			height = pyNum{}
			switch autoLeading {
			case "max":
				for _, l := range p.fragLines {
					height = numAddOrErr(height, maxFloatNum(l.ascent-l.descent, style.leading))
				}
			case "min":
				for _, l := range p.fragLines {
					height = numAddOrErr(height, numFloat(l.ascent-l.descent))
				}
			default:
				panic(paraFailure{"invalid autoLeading value"})
			}
		} else {
			height = numMulOrErr(numInt(int64(p.lineCount())), style.leading)
		}
	default:
		leading := style.leading
		switch autoLeading {
		case "max":
			leading = numMax(leading, numSubOrErr(p.ascent, p.descent))
		case "min":
			leading = numSubOrErr(p.ascent, p.descent)
		}
		height = numMulOrErr(numInt(int64(p.lineCount())), leading)
	}
	p.heightNum = height
	p.height = height.float()
	return p.width, p.height
}

// simplePart is _split_blParaSimple: the words of some lines, the pieces of
// a long word joined again.
func (p *paragraph) simplePart(lines []simpleLine) *paragraph {
	part := p.lineFrag.clone()
	part.text, part.hasWords, part.words = "", true, []*simpleWord{}
	var pieces []*simpleWord
	join := func() {
		joined := &simpleWord{}
		for _, piece := range pieces {
			joined.text = append(joined.text, piece.text...)
		}
		part.words = append(part.words, joined)
		pieces = nil
	}
	for _, line := range lines {
		for _, word := range line.words {
			if word.split {
				pieces = append(pieces, word)
				if word.end {
					join()
				}
				continue
			}
			if len(pieces) > 0 {
				join()
			}
			part.words = append(part.words, word)
		}
	}
	if len(pieces) > 0 {
		join()
	}
	return &paragraph{style: p.style, frags: []*frag{part}}
}

// processedPart is Paragraph._split_blParaProcessed.
func (p *paragraph) processedPart(start, stop int) *paragraph {
	from, to := p.fragLines[start].sFW, len(p.processed)
	if stop != len(p.fragLines) {
		to = p.fragLines[stop].sFW
	}
	words := append([]*fragWord{}, p.processed[from:to]...)
	for len(words) > 0 && words[len(words)-1].injected {
		words = words[:len(words)-1]
	}
	if len(words) == 0 {
		panic(paraFailure{"list index out of range"})
	}
	if last := words[len(words)-1]; last.splitHY {
		// _SHYWordHS (hyphenation, the other source of this class, is off).
		last.shy, last.split, last.hs, last.splitH, last.splitHY = true, true, true, false, false
	}
	return &paragraph{style: p.style, processed: words}
}

// split is Paragraph.split: nothing when fewer than two lines fit, the whole
// paragraph when all do, else the lines that fit and the rest as two
// paragraphs (no orphan line; a last line alone is allowed). The first part
// keeps the bullet, the second drops it, and only the second keeps the
// endDots of the style.
func (p *paragraph) split(c *canvas, availWidth, availHeight float64) []flowable {
	if p.count() == 0 || availWidth < fuzz || availHeight < fuzz {
		return nil
	}
	if !p.wrapped {
		p.wrap(c, availWidth, availHeight)
	}
	style := p.style
	autoLeading := style.autoLeading
	s := 0
	height := pyNum{}
	if p.kind == 1 && autoLeading != "" && autoLeading != "off" {
		limit := numFloat(availHeight + 1e-8)
		switch autoLeading {
		case "max":
			for i, l := range p.fragLines {
				n := numAddOrErr(height, maxFloatNum(l.ascent-l.descent, style.leading))
				if numLess(limit, n) {
					break
				}
				height = n
				s = i + 1
			}
		case "min":
			for i, l := range p.fragLines {
				n := numAddOrErr(height, numFloat(l.ascent-l.descent))
				if numLess(limit, n) {
					break
				}
				height = n
				s = i + 1
			}
		default:
			panic(paraFailure{"invalid autoLeading value"})
		}
	} else {
		l := style.leading
		switch autoLeading {
		case "max":
			l = numMax(l, numMulOrErr(numFloat(1.2), style.fontSize))
		case "min":
			l = numMulOrErr(numFloat(1.2), style.fontSize)
		}
		s = lineCountOf(availHeight / l.float())
		height = numMulOrErr(numInt(int64(s)), l)
	}
	if !p.style.allowOrphans && s <= 1 || s == 0 {
		p.wrapped = false
		return nil
	}
	n := p.lineCount()
	if n <= s {
		return []flowable{p}
	}
	if p.style.denyWidows && n == s+1 {
		if p.style.allowOrphans && n == 3 || n > 3 {
			s--
		} else {
			p.wrapped = false
			return nil
		}
	}
	style1 := p.style
	if style1.endDots != "" {
		style1.endDots = ""
	}
	var first, second *paragraph
	if p.kind == 1 {
		first, second = p.processedPart(0, s), p.processedPart(s, n)
	} else {
		first, second = p.simplePart(p.lines[:s]), p.simplePart(p.lines[s:])
	}
	first.style, first.bullet = style1, p.bullet
	first.justifyLast = !(p.kind == 1 && p.fragLines[s-1].lineBreak)
	first.splitPara = true
	first.heightNum = height
	first.height = height.float()
	first.width = availWidth
	if style.firstLineIndent.truthy() {
		style.firstLineIndent = pyNum{}
		second.style = style
	}
	return []flowable{first, second}
}

// lineCountOf is int(n) of a line count: a NaN or an infinity is int()'s
// ValueError or OverflowError (a failure of the build), and a count beyond int
// saturates because it is larger than any paragraph's lines.
func lineCountOf(n float64) int {
	if math.IsNaN(n) {
		panic(paraFailure{"cannot convert float NaN to integer"})
	}
	if math.IsInf(n, 0) {
		panic(paraFailure{"cannot convert float infinity to integer"})
	}
	if n >= float64(math.MaxInt32) {
		return math.MaxInt32
	}
	if n <= float64(math.MinInt32) {
		return math.MinInt32
	}
	return int(n)
}

// minWidth is Paragraph.minWidth: the widest word.
func (p *paragraph) minWidth() float64 {
	if p.count() == 0 {
		return 0
	}
	widest := 0.0
	if p.processed == nil && len(p.frags) == 1 {
		f := p.frags[0]
		var words []string
		if f.hasWords {
			for _, word := range f.words {
				words = append(words, string(word.text))
			}
		} else {
			words = strings.Split(f.text, " ")
		}
		for i, word := range words {
			if w := stringWidth(word, f.font, f.size.float()); i == 0 || w > widest {
				widest = w
			}
		}
		return widest
	}
	for i, word := range p.fragWords(0, false) {
		if i == 0 || word.width > widest {
			widest = word.width
		}
	}
	return widest
}

// offsetsOf is drawPara's _offsets: the x offset of each line.
func (p *paragraph) offsetsOf(nLines int) []float64 {
	offsets := append([]float64{}, p.offsets...)
	if len(offsets) == 0 {
		offsets = []float64{0}
	}
	if n := nLines - len(offsets); n > 0 {
		for i := 0; i < n; i++ {
			offsets = append(offsets, offsets[len(offsets)-1])
		}
	}
	return offsets
}

// _56 and _16 of _putFragLine's autoLeading 'max' bounds.
const (
	fiveSixths = 5.0 / 6.0
	oneSixth   = 1.0 / 6.0
)
