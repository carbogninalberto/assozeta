package pdfexport

import (
	"fmt"
	"strconv"
	"strings"
)

// _FK_TEXT and _FK_BREAK of paragraph.py.
const (
	fragText = iota
	fragBreak
)

// frag is a ParaFrag: a run of text in one font, or a line break. A part of
// a split one-font paragraph carries words instead of text.
type frag struct {
	font      *font
	text      string
	words     []*simpleWord
	hasWords  bool
	lineBreak bool
	kind      int
}

func (f *frag) clone() *frag {
	copied := *f
	return &copied
}

// paraNotPortedTags are the tags of ParaParser whose effect on the layout is
// not ported (lines, links, images, rises, other sizes and colours, bullets,
// sequences and callbacks).
var paraNotPortedTags = map[string]bool{"a": true, "bullet": true, "font": true, "greek": true, "img": true, "index": true,
	"link": true, "nobr": true, "ondraw": true, "seq": true, "seqchain": true, "seqdefault": true, "seqformat": true,
	"seqreset": true, "span": true, "strike": true, "sub": true, "sup": true, "super": true, "u": true, "unichar": true}

type paraTag struct {
	tag          string
	bold, italic bool
	selfClosing  bool
	lineBreak    bool
}

// paraParser is reportlab's ParaParser for the tags that only change the
// font or break the line; every other known tag is refused.
type paraParser struct {
	family       string
	bold, italic bool
	started      bool
	stack        []paraTag
	frags        []*frag
	err          error
}

func (p *paraParser) fail(format string, args ...any) bool {
	if p.err == nil {
		p.err = fmt.Errorf("%w: "+format, append([]any{ErrLayout}, args...)...)
	}
	return false
}

func (p *paraParser) push(tag string, change func(*paraTag)) bool {
	if len(p.stack) == 0 {
		return p.fail("tag outside the paragraph")
	}
	top := p.stack[len(p.stack)-1]
	top.tag = tag
	if change != nil {
		change(&top)
	}
	p.stack = append(p.stack, top)
	return true
}

func (p *paraParser) pop(tag string) bool {
	if len(p.stack) == 0 {
		return p.fail("pop from empty list")
	}
	top := p.stack[len(p.stack)-1]
	p.stack = p.stack[:len(p.stack)-1]
	if top.tag != tag {
		return p.fail("Parse error: saw </%s> instead of expected </%s>", tag, top.tag)
	}
	return true
}

func (p *paraParser) startTag(tag string, attrs []string) bool {
	switch tag {
	case "para":
		if len(attrs) > 0 {
			p.err = ErrNotPorted
			return false
		}
		p.started = true
		p.stack = []paraTag{{tag: "para", bold: p.bold, italic: p.italic}}
		return true
	case "b", "strong":
		return p.push(tag, func(t *paraTag) { t.bold = true })
	case "i", "em":
		return p.push(tag, func(t *paraTag) { t.italic = true })
	case "br":
		return p.push(tag, func(t *paraTag) { t.selfClosing, t.lineBreak = true, true })
	}
	if paraNotPortedTags[tag] {
		p.err = ErrNotPorted
		return false
	}
	return true
}

func (p *paraParser) endTag(tag string) bool {
	switch tag {
	case "para", "b", "strong", "i", "em":
		return p.pop(tag)
	case "br":
		if len(p.stack) == 0 || !p.stack[len(p.stack)-1].selfClosing {
			return p.fail("Parser failure in <br/>")
		}
		p.stack[len(p.stack)-1].selfClosing = false
		return p.data("") && p.pop("br")
	}
	if paraNotPortedTags[tag] {
		p.err = ErrNotPorted
		return false
	}
	return true
}

func (p *paraParser) data(text string) bool {
	if len(p.stack) == 0 {
		return p.fail("text outside the paragraph")
	}
	top := p.stack[len(p.stack)-1]
	if top.selfClosing {
		if text != "" {
			return p.fail("No content allowed in br tag")
		}
		return true
	}
	name := p.family
	switch {
	case top.bold && top.italic:
		name += "-BoldOblique"
	case top.bold:
		name += "-Bold"
	case top.italic:
		name += "-Oblique"
	}
	p.frags = append(p.frags, &frag{font: fontsByName[name], text: text, lineBreak: top.lineBreak})
	return true
}

func (p *paraParser) entityRef(name string) bool {
	if value, ok := paraEntities[name]; ok {
		return p.data(value)
	}
	return p.data("&" + name + ";")
}

func (p *paraParser) charRef(name string) bool {
	digits, base := name, 10
	if name[0] == 'x' {
		digits, base = name[1:], 16
	} else if len(name) > 4300 {
		return p.fail("character reference")
	}
	digits = strings.TrimLeft(digits, "0")
	if len(digits) > 7 {
		return p.fail("character reference")
	}
	n := int64(0)
	if digits != "" {
		var err error
		if n, err = strconv.ParseInt(digits, base, 64); err != nil {
			return p.fail("character reference")
		}
	}
	if n > 0x10FFFF {
		return p.fail("character reference")
	}
	return p.data(string(rune(n)))
}

// paraIsPara is ParaParser.parse's test for text that brings its own <para>.
func paraIsPara(text []rune) bool {
	if len(text) < 6 || text[0] != '<' {
		return false
	}
	i := 1
	for i < len(text) && strings.ContainsRune(pySpaces, text[i]) {
		i++
	}
	if i+4 > len(text) || string(text[i:i+4]) != "para" {
		return false
	}
	i += 4
	if i >= len(text) {
		return false
	}
	return strings.ContainsRune(pySpaces, text[i]) || text[i] == '>' || text[i] == '/' && i+1 < len(text) && text[i+1] == '>'
}

// parseParagraph is ParaParser.parse of cleaned text in a Helvetica style:
// the fragments, ErrLayout where reportlab raises, ErrNotPorted for the tags
// above.
func parseParagraph(text string, style paraStyle) ([]*frag, error) {
	base := fontsByName[style.fontName]
	if !strings.ContainsAny(text, "<&") {
		if text == "" {
			return nil, nil
		}
		return []*frag{{font: base, text: text}}, nil
	}
	parser := &paraParser{family: "Helvetica", bold: strings.Contains(style.fontName, "Bold"), italic: strings.Contains(style.fontName, "Oblique")}
	raw := []rune(text)
	if !paraIsPara(raw) {
		raw = []rune("<para>" + text + "</para>")
	}
	ok := (&htmlParser{raw: raw, events: parser}).feed()
	if parser.err != nil {
		return nil, parser.err
	}
	if !ok || !parser.started || len(parser.stack) > 0 {
		return nil, fmt.Errorf("%w: paragraph markup", ErrLayout)
	}
	return parser.frags, nil
}
