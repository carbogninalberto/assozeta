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
}

// styleNormal is getSampleStyleSheet()['Normal'].
func styleNormal() paraStyle {
	return paraStyle{fontName: "Helvetica", fontSize: 10, leading: 12}
}

type paraWord struct {
	text  string
	split bool // a _SplitWord piece of a long word
}

type paraLine struct {
	extra float64
	words []string
}

// paragraph is platypus.Paragraph for text without markup: one fragment in
// the style's font (reportlab's "kind 0" lines).
type paragraph struct {
	style  paraStyle
	text   string
	lines  []paraLine
	width  float64
	height float64
}

// Python's str.isspace() characters and reportlab's _wsc (which has U+200B
// and leaves the no-break space alone).
const (
	pySpaces = "\t\n\v\f\r\x1c\x1d\x1e\x1f \u0085                 　"
	rlSpaces = "\t\n\v\f\r\x1c\x1d\x1e\x1f \u0085            ​    　"
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

// newParagraph is Paragraph(text, style). Markup ('<'), entities ('&') and
// soft hyphens take reportlab's parser and multi-fragment layout, which are
// not ported.
func newParagraph(text string, style paraStyle) (*paragraph, error) {
	if strings.ContainsAny(text, "<&­") {
		return nil, ErrNotPorted
	}
	return &paragraph{style: style, text: paraClean(text)}, nil
}

func (p *paragraph) spaceBefore() float64 { return p.style.spaceBefore }
func (p *paragraph) spaceAfter() float64  { return p.style.spaceAfter }
func (p *paragraph) hAlign() string       { return "LEFT" }

// splitWord is paragraph._splitWord.
func (p *paragraph) splitWord(word string, lineWidth float64, maxWidth float64) []paraWord {
	f := fontsByName[p.style.fontName]
	var out []paraWord
	var current strings.Builder
	for _, r := range word {
		cw := stringWidth(string(r), f, p.style.fontSize)
		newWidth := lineWidth + cw
		if newWidth > maxWidth {
			out = append(out, paraWord{text: current.String(), split: true})
			newWidth = cw
			current.Reset()
		}
		current.WriteRune(r)
		lineWidth = newWidth
	}
	return append(out, paraWord{text: current.String(), split: true})
}

// breakLines is Paragraph.breakLines for one fragment (both line widths are
// the available width: the styles have no indents).
func (p *paragraph) breakLines(maxWidth float64) {
	p.lines = nil
	text := strings.Trim(p.text, rlSpaces)
	if text == "" {
		return
	}
	f := fontsByName[p.style.fontName]
	size := p.style.fontSize
	var words []paraWord
	for _, word := range paraSplit(text) {
		words = append(words, paraWord{text: word})
	}
	spaceWidth := stringWidth(" ", f, size)
	shrink := 0.05 * spaceWidth
	var line []string
	currentWidth := -spaceWidth
	forced := false
	for len(words) > 0 {
		word := words[0]
		words = words[1:]
		if word.text == "" && word.split {
			forced = true
		}
		wordWidth := stringWidth(word.text, f, size)
		newWidth := currentWidth + spaceWidth + wordWidth
		limWidth := maxWidth + shrink*float64(len(line))
		if newWidth > limWidth && !forced {
			if !word.split && wordWidth > maxWidth {
				words = append(p.splitWord(word.text, currentWidth+spaceWidth, maxWidth), words...)
				forced = true
				continue
			}
		}
		if newWidth <= limWidth || len(line) == 0 || forced {
			if word.text != "" {
				line = append(line, word.text)
			}
			if forced {
				forced = false
				p.lines = append(p.lines, paraLine{maxWidth - newWidth, line})
				line = nil
				currentWidth = -spaceWidth
			} else {
				currentWidth = newWidth
			}
		} else {
			p.lines = append(p.lines, paraLine{maxWidth - currentWidth, line})
			line = []string{word.text}
			currentWidth = wordWidth
		}
	}
	if len(line) > 0 {
		p.lines = append(p.lines, paraLine{maxWidth - currentWidth, line})
	}
}

func (p *paragraph) wrap(c *canvas, availWidth, availHeight float64) (float64, float64) {
	if availWidth < fuzz {
		return 0, 0x7fffffff
	}
	p.width = availWidth
	p.breakLines(availWidth)
	p.height = float64(len(p.lines)) * p.style.leading
	return p.width, p.height
}

// split is Paragraph.split: nothing when fewer than two lines fit, the whole
// paragraph when all do. A paragraph cut between two pages is not ported.
func (p *paragraph) split(c *canvas, availWidth, availHeight float64) []flowable {
	if p.text == "" || availWidth < fuzz || availHeight < fuzz {
		return nil
	}
	p.wrap(c, availWidth, availHeight)
	s := int(availHeight / p.style.leading)
	if s <= 1 {
		return nil
	}
	if len(p.lines) <= s {
		return []flowable{p}
	}
	c.err = ErrNotPorted
	return nil
}

// minWidth is Paragraph.minWidth: the widest word.
func (p *paragraph) minWidth() float64 {
	if p.text == "" {
		return 0
	}
	f := fontsByName[p.style.fontName]
	widest := 0.0
	for i, word := range strings.Split(p.text, " ") {
		if w := stringWidth(word, f, p.style.fontSize); i == 0 || w > widest {
			widest = w
		}
	}
	return widest
}

func xPos(parts []string, dx float64) []string {
	if dx > 1e-6 || dx < -1e-6 {
		return append(parts, fpStr(dx)+" 0 Td")
	}
	return parts
}

// draw is Paragraph.drawPara for left-aligned and centred lines.
func (p *paragraph) draw(c *canvas) {
	if len(p.lines) == 0 {
		return
	}
	c.saveState()
	c.setFillColor(colorBlack)
	f := fontsByName[p.style.fontName]
	size, leading := p.style.fontSize, p.style.leading
	parts := []string{"BT", "1 0 0 1 " + fpStrs(0, p.height-size) + " Tm", c.fontRef(f) + " " + fpStr(size) + " Tf " + fpStr(leading) + " TL"}
	for _, line := range p.lines {
		text := strings.Join(line.words, " ")
		simple := line.extra > -1e-8
		spaces := 0
		if !simple {
			spaces = len(line.words) + strings.Count(text, " ") - 1
			simple = spaces <= 0
		}
		out := c.formatText(text, f, size, leading) + " T*"
		if simple {
			m := 0.0
			if p.style.centred {
				m = 0 + 0.5*line.extra
			}
			parts = xPos(parts, m)
			parts = append(parts, out)
			parts = xPos(parts, -m)
			continue
		}
		parts = append(parts, fpStr(line.extra/float64(spaces))+" Tw", out, "0 Tw")
	}
	c.add(strings.Join(append(parts, "ET"), " "))
	c.restoreState()
}
