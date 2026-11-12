package pdfexport

import (
	"strings"
)

// paraStyle is the part of ParagraphStyle the documents set.
type paraStyle struct {
	fontName    string
	fontSize    float64
	leading     float64
	centred     bool
	spaceBefore float64
	spaceAfter  float64
	textColor   color
	align       int
	backColor   *color
	// shrink is spaceShrinkage when shrinkSet; denyWidows, allowOrphans and
	// noSplit are allowWidows=0, allowOrphans=1 and splitLongWords=0.
	shrink       float64
	shrinkSet    bool
	denyWidows   bool
	allowOrphans bool
	noSplit      bool
	// lines are the underline and strike defaults of the style; linesSet is
	// false for a style built by a document, which has reportlab's.
	lines    [2]lineDefaults
	linesSet bool
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

// alignment is the alignment of the style: centred for the documents'
// centred styles, else align.
func (s paraStyle) alignment() int {
	if s.centred {
		return alignCenter
	}
	return s.align
}

// shrinkage is spaceShrinkage.
func (s paraStyle) shrinkage() float64 {
	if s.shrinkSet {
		return s.shrink
	}
	return 0.05
}

func defaultLines() [2]lineDefaults {
	return [2]lineDefaults{
		lineUnderline: {width: usVal{}, offset: usVal{str: "-0.125*F"}, gap: usVal{str: "1"}},
		lineStrike:    {width: usVal{}, offset: usVal{str: "0.25*F"}, gap: usVal{str: "1"}},
	}
}

// styleNormal is getSampleStyleSheet()['Normal'].
func styleNormal() paraStyle {
	return paraStyle{fontName: "Helvetica", fontSize: 10, leading: 12}
}

// paraFailure is an exception reportlab raises while it lays a paragraph
// out; docTemplate.build answers it as ErrLayout.
type paraFailure struct{ reason string }

const (
	softHyphen = '­'
	fsNever    = float64(0x7fffffff)
)

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
	fontSize  float64
	sFW       int
}

// paragraph is platypus.Paragraph: the fragments of the parser, laid out as
// lines of one font (kind 0) or of several fragments (kind 1).
type paragraph struct {
	style     paraStyle
	frags     []*frag
	processed []*fragWord // self.frags once it is a frag word list
	isWords   bool
	wrapped   bool
	kind      int
	lines     []simpleLine
	fragLines []*fragLine
	lineFrag  *frag
	width     float64
	height    float64
}

// Python's str.isspace() characters and reportlab's _wsc (which has U+200B
// and leaves the no-break space alone).
const (
	pySpaces = "\t\n\v\f\r\x1c\x1d\x1e\x1f \u0085                 　"
	rlSpaces = "\t\n\v\f\r\x1c\x1d\x1e\x1f \u0085            ​    　"
	// string.whitespace
	asciiSpaces = " \t\n\r\v\f"
)

// paraSplit is paragraph.split(text): str.split(), or a split on reportlab's
// own whitespace when the text has a no-break space (re.split keeps the empty
// strings at the ends).
func paraSplit(text string) []string {
	if !strings.ContainsRune(text, ' ') {
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

// newParagraph is Paragraph(text, style): the text cleaned and parsed.
func newParagraph(text string, style paraStyle) (*paragraph, error) {
	frags, style, err := parseParagraph(paraClean(text), style)
	if err != nil {
		return nil, err
	}
	return &paragraph{style: style, frags: frags}, nil
}

func (p *paragraph) spaceBefore() float64 { return p.style.spaceBefore }
func (p *paragraph) spaceAfter() float64  { return p.style.spaceAfter }
func (p *paragraph) hAlign() string       { return "LEFT" }

func (p *paragraph) count() int {
	if p.processed != nil {
		return len(p.processed)
	}
	return len(p.frags)
}

// splitWord is paragraph._splitWord.
func splitWord(word []rune, lineWidth, maxWidth float64, f *font, size float64) []*simpleWord {
	var out []*simpleWord
	var current []rune
	for _, r := range word {
		cw := stringWidth(string(r), f, size)
		newWidth := lineWidth + cw
		if newWidth > maxWidth {
			out = append(out, &simpleWord{text: current, split: true})
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

// breakSimple is Paragraph.breakLines for one fragment (both line widths are
// the available width: the styles have no indents).
func (p *paragraph) breakSimple(maxWidth float64) {
	f := p.frags[0]
	p.kind, p.lines, p.lineFrag = 0, nil, f
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
	size := f.size
	spaceWidth := stringWidth(" ", f.font, size)
	shrink := p.style.shrinkage() * spaceWidth
	hyphenWidth := stringWidth("-", f.font, size)
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
		limWidth := maxWidth + shrink*float64(len(line))
		dropped := false
		if newWidth > limWidth && !forced {
			if word.shy {
				if parts := word.shySplit(f.font, size, currentWidth+spaceWidth+hyphenWidth-1e-8, limWidth); parts != nil {
					words = append(parts, words...)
					forced = true
					continue
				} else if len(line) > 0 && word.fsww+hyphenWidth+1e-8 <= maxWidth {
					if word.shySplit(f.font, size, hyphenWidth-1e-8, maxWidth) != nil {
						words = append([]*simpleWord{word}, words...)
						forced, dropped = true, true
						newWidth = currentWidth
					}
				}
			}
			if !p.style.noSplit && !word.split && !forced && wordWidth > maxWidth {
				words = append(splitWord(word.text, currentWidth+spaceWidth, maxWidth, f.font, size), words...)
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
			} else {
				currentWidth = newWidth
			}
		} else {
			p.lines = append(p.lines, simpleLine{maxWidth - currentWidth, line})
			line = []*simpleWord{word}
			currentWidth = wordWidth
		}
	}
	if len(line) > 0 {
		p.lines = append(p.lines, simpleLine{maxWidth - currentWidth, line})
	}
}

func fragWidth(text []rune, f *frag) float64 {
	return stringWidth(string(text), f.font, f.size)
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
		case f.img != nil:
			width := f.img.width.norm(maxWidth)
			if f.img.width.pct && !hasMax {
				panic(paraFailure{"unsupported operand type(s) for *: 'NoneType' and 'float'"})
			}
			if f.img.width.v != 0 {
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
func splitFragWord(w *fragWord, maxWidth, lineWidthMax float64) []*fragWord {
	var out []*fragWord
	var parts []fragPart
	lineWidth, wordWidth := 0.0, 0.0
	var text []rune
	f := w.parts[0].f
	step := func(g *frag, cw float64, c []rune) {
		newLineWidth := lineWidth + cw
		tooLong := newLineWidth > maxWidth
		if g != f || tooLong {
			f = f.clone()
			f.text = string(text)
			parts = append(parts, fragPart{f, shyText{s: text}})
			if tooLong {
				out = append(out, &fragWord{width: wordWidth, parts: parts, split: true})
				maxWidth = lineWidthMax
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
func (p *paragraph) breakFrags(maxWidth float64) {
	p.kind, p.fragLines = 1, nil
	pending := p.fragWords(maxWidth, true)
	var words []*frag
	var done []*fragWord
	var g *frag
	sFW, n := 0, 0
	spaceWidth, currentWidth := 0.0, 0.0
	maxSize := 0.0
	injected := func(f *frag) *fragWord {
		broken := f.clone()
		broken.kind, broken.text = fragBreak, ""
		return &fragWord{injected: true, parts: []fragPart{{broken, shyText{}}}}
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
					spaceShrink += float64(ns) * stringWidth(" ", wi.font, wi.size)
				}
			}
		}
		limWidth := maxWidth + spaceShrink*p.style.shrinkage()
		if !lineBreak && newWidth > limWidth && !w.splitH && f.img == nil {
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
				cut := splitFragWord(w, maxWidth-spaceWidth-currentWidth, maxWidth)
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
			maxSize = max(maxSize, f.size)
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
			spaceWidth = follow(f.size)
			more := false
			for _, part := range w.parts[1:] {
				g = part.f.clone()
				g.text = string(part.text.s)
				if g.text != "" {
					more = true
				}
				words = append(words, g)
				maxSize = max(maxSize, g.size)
			}
			if text == "" && more {
				n++
			}
			currentWidth = newWidth
			continue
		}
		if lineBreak {
			g = f.clone()
			words = append(words, g)
		}
		p.fragLines = append(p.fragLines, &fragLine{extra: maxWidth - currentWidth, wordCount: n, words: words, fontSize: maxSize, sFW: sFW})
		sFW = len(done) - 1
		if lineBreak {
			words = nil
			continue
		}
		spaceWidth = follow(lastSize)
		currentWidth = wordWidth
		n = 1
		g = f.clone()
		maxSize = g.size
		words = []*frag{g}
		g.text = string(w.parts[0].text.s)
		for _, part := range w.parts[1:] {
			g = part.f.clone()
			g.text = string(part.text.s)
			words = append(words, g)
			maxSize = max(maxSize, g.size)
		}
	}
	if len(words) > 0 {
		p.fragLines = append(p.fragLines, &fragLine{extra: maxWidth - currentWidth, wordCount: n, words: words, fontSize: maxSize, sFW: sFW})
	}
	p.processed = done
}

// simpleKind is the test of Paragraph.breakLines for the one-font layout: a
// single fragment that is no image and has no background.
func (p *paragraph) simpleKind() bool {
	return len(p.frags) == 1 && p.frags[0].img == nil && p.frags[0].back == nil
}

func (p *paragraph) lineCount() int {
	if p.kind == 1 {
		return len(p.fragLines)
	}
	return len(p.lines)
}

func (p *paragraph) wrap(c *canvas, availWidth, availHeight float64) (float64, float64) {
	if availWidth < fuzz {
		return 0, 0x7fffffff
	}
	p.width = availWidth
	p.wrapped = true
	switch {
	case p.count() == 0:
		p.kind, p.lines, p.fragLines, p.lineFrag = 0, nil, nil, &frag{font: fontsByName[p.style.fontName], size: p.style.fontSize, color: p.style.textColor}
	case p.processed == nil && p.simpleKind():
		p.breakSimple(availWidth)
	default:
		p.breakFrags(availWidth)
	}
	p.height = float64(p.lineCount()) * p.style.leading
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
// paragraphs (no orphan line; a last line alone is allowed).
func (p *paragraph) split(c *canvas, availWidth, availHeight float64) []flowable {
	if p.count() == 0 || availWidth < fuzz || availHeight < fuzz {
		return nil
	}
	if !p.wrapped {
		p.wrap(c, availWidth, availHeight)
	}
	s := int(availHeight / p.style.leading)
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
	if p.kind == 1 {
		return []flowable{p.processedPart(0, s), p.processedPart(s, n)}
	}
	return []flowable{p.simplePart(p.lines[:s]), p.simplePart(p.lines[s:])}
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
			if w := stringWidth(word, f.font, f.size); i == 0 || w > widest {
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
