package pdfexport

import (
	"fmt"
	"strconv"
	"strings"
)

// _FK_TEXT, _FK_IMG, _FK_APPEND and _FK_BREAK of paragraph.py.
const (
	fragText = iota
	fragImage
	fragAppend
	fragBreak
)

// usVal is the width, offset or gap of an underline or strike: the string of
// the markup (or of the style), or the number a '*P' value became.
type usVal struct {
	str   string
	num   float64
	isNum bool
}

const (
	lineUnderline = iota
	lineStrike
)

// usLine is a tuple of ParaFrag.us_lines: (n, kind, colour, width, offset,
// rise, repeats, gap).
type usLine struct {
	n                  int
	kind               int
	hasColor           bool
	color              color
	width, offset, gap usVal
	rise               float64
	mult               int
}

// linkRef is a tuple of ParaFrag.link: (n, target).
type linkRef struct {
	n      int
	target string
}

// frag is a ParaFrag: a run of text in one font, size and colour, or a line
// break, or an inline image. A part of a split one-font paragraph carries
// words instead of text.
type frag struct {
	font      *font
	size      float64
	color     color
	rise      float64
	text      string
	words     []*simpleWord
	hasWords  bool
	lineBreak bool
	kind      int
	us        []usLine
	links     []linkRef
	back      *color
	nobr      bool
	img       *imgDefn
}

func (f *frag) clone() *frag {
	copied := *f
	return &copied
}

func sameUS(a, b []usLine) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func sameLinks(a, b []linkRef) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func sameBack(a, b *color) bool {
	if a == nil || b == nil {
		return a == b
	}
	return *a == *b
}

// sameFrag is rl_accel.sameFrag.
func sameFrag(f, g *frag) bool {
	if f.img != nil || g.img != nil || f.lineBreak || g.lineBreak {
		return false
	}
	return f.font == g.font && f.size == g.size && f.color == g.color && f.rise == g.rise && sameUS(f.us, g.us) &&
		sameLinks(f.links, g.links) && sameBack(f.back, g.back) && f.nobr == g.nobr
}

// imgDefn is the cbDefn of an <img> fragment.
type imgDefn struct {
	src           string
	image         *jpegImage
	width, height float64
	valign        string
}

// paraPorted are the tags whose effect on the layout is reproduced. Every
// other tag ParaParser has a handler for is refused; tags it does not know
// are ignored, as it does.
var paraPorted = map[string]bool{"para": true, "b": true, "strong": true, "i": true, "em": true, "br": true, "u": true, "strike": true}

// paraNotPortedTags are the tags of ParaParser that are still refused.
var paraNotPortedTags = map[string]bool{"a": true, "bullet": true, "font": true, "greek": true, "img": true, "index": true,
	"link": true, "nobr": true, "ondraw": true, "seq": true, "seqchain": true, "seqdefault": true, "seqformat": true,
	"seqreset": true, "span": true, "sub": true, "sup": true, "super": true, "unichar": true}

// lineAttr is what <u> and <strike> (and <link underline>) set on a frag.
type lineAttr struct {
	hasColor           bool
	color              color
	width, offset, gap *usVal
	kind               string
}

type paraTag struct {
	tag          string
	family       string
	bold, italic bool
	size         float64
	color        color
	back         *color
	rise         float64
	us           []usLine
	links        []linkRef
	line         [2]lineAttr
	nobr         bool
	selfClosing  string
	lineBreak    bool
	img          *imgDefn
}

// paraParser is reportlab's ParaParser.
type paraParser struct {
	style   paraStyle
	family  string
	started bool
	stack   []paraTag
	frags   []*frag
	err     error
	nlines  int
	nlinks  int
}

func (p *paraParser) fail(format string, args ...any) bool {
	if p.err == nil {
		p.err = fmt.Errorf("%w: "+format, append([]any{ErrLayout}, args...)...)
	}
	return false
}

func (p *paraParser) unsupported() bool {
	if p.err == nil {
		p.err = ErrNotPorted
	}
	return false
}

// attrFailure answers the status of a conversion: an exception of reportlab
// or an input that is not ported.
func (p *paraParser) attrFailure(st attrStatus, what string) bool {
	if st == attrUnsupported {
		return p.unsupported()
	}
	return p.fail("%s", what)
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

func (p *paraParser) pop(tag string) (paraTag, bool) {
	if len(p.stack) == 0 {
		return paraTag{}, p.fail("pop from empty list")
	}
	top := p.stack[len(p.stack)-1]
	p.stack = p.stack[:len(p.stack)-1]
	if top.tag != tag {
		return top, p.fail("Parse error: saw </%s> instead of expected </%s>", tag, top.tag)
	}
	return top, true
}

// attrMap is a map of attribute names: getAttributes(attr, attrMap).
type attrKind int

const (
	attrSize attrKind = iota
	attrFace
	attrColor
	attrUSWidth
	attrUSOffset
	attrUSGap
	attrUSKind
	attrRise
	attrSupSize
)

type attrSpec struct {
	key  string
	kind attrKind
}

// paraAttrs is the result of getAttributes for the attributes the ported
// tags take.
type paraAttrs struct {
	size                     *numVal
	family                   string
	hasFamily                bool
	bold, italic             bool
	color, back              *color
	usColor                  *color
	usWidth, usOffset, usGap *usVal
	usKind                   string
	hasKind                  bool
	supRise, supSize         string
	hasRise, hasSize         bool
}

func lookupAttr(m map[string]attrSpec, name string) (attrSpec, bool) {
	spec, ok := m[name]
	return spec, ok
}

var (
	// _uAttrMap and _strikeAttrMap
	underlineAttrs = map[string]attrSpec{"color": {"underlineColor", attrColor}, "width": {"underlineWidth", attrUSWidth},
		"offset": {"underlineOffset", attrUSOffset}, "gap": {"underlineGap", attrUSGap}, "kind": {"underlineKind", attrUSKind}}
	strikeAttrs = map[string]attrSpec{"color": {"strikeColor", attrColor}, "width": {"strikeWidth", attrUSWidth},
		"offset": {"strikeOffset", attrUSOffset}, "gap": {"strikeGap", attrUSGap}, "kind": {"strikeKind", attrUSKind}}
)

// usValue is _CheckUS: the stripped string, or the number a '*P' value is.
func (p *paraParser) usValue(raw string, has bool) (*usVal, bool) {
	if !has {
		p.fail("invalid value for a line attribute")
		return nil, false
	}
	if !asciiOnly(raw) {
		p.unsupported()
		return nil, false
	}
	s := pyTrimASCII(raw)
	if s != "" {
		if idx := strings.LastIndex(s, "*"); idx >= 0 && !strings.Contains(s[:idx], "\n") && len(pyTrimASCII(s[idx+1:])) == 1 &&
			strings.Contains("PLfF", pyTrimASCII(s[idx+1:])) {
			v, st := pyFloat(s[:idx])
			if st != attrOK {
				p.attrFailure(st, "invalid value for a line attribute")
				return nil, false
			}
			if pyTrimASCII(s[idx+1:]) == "P" {
				return &usVal{num: p.stack[0].size * v, isNum: true}, true
			}
		} else if _, st := paraNum(s, false); st != attrOK {
			p.attrFailure(st, "invalid value for a line attribute")
			return nil, false
		}
	}
	return &usVal{str: s}, true
}

// lineAttributes is getAttributes(attr, _uAttrMap or _strikeAttrMap).
func (p *paraParser) lineAttributes(attrs []htmlAttr, specs map[string]attrSpec, into *lineAttr) bool {
	order, values := dedupeAttrs(attrs)
	for _, name := range order {
		attr := values[name]
		spec, ok := lookupAttr(specs, name)
		if !ok {
			return p.fail("invalid attribute name %s", name)
		}
		switch spec.kind {
		case attrColor:
			if !attr.has {
				return p.fail("invalid color")
			}
			c, st := paraColor(attr.value)
			if st != attrOK {
				return p.attrFailure(st, "invalid color")
			}
			into.hasColor, into.color = true, c
		case attrUSWidth, attrUSOffset, attrUSGap:
			v, ok := p.usValue(attr.value, attr.has)
			if !ok {
				return false
			}
			switch spec.kind {
			case attrUSWidth:
				into.width = v
			case attrUSOffset:
				into.offset = v
			default:
				into.gap = v
			}
		case attrUSKind:
			if !attr.has {
				return p.fail("invalid kind")
			}
			into.kind = attr.value
		}
	}
	return true
}

// dedupeAttrs is dict(attrs): the first position of a name, the last value.
func dedupeAttrs(attrs []htmlAttr) ([]string, map[string]htmlAttr) {
	var order []string
	values := map[string]htmlAttr{}
	for _, attr := range attrs {
		if _, seen := values[attr.name]; !seen {
			order = append(order, attr.name)
		}
		values[attr.name] = attr
	}
	return order, values
}

var lineRepeats = map[string]int{"single": 1, "double": 2, "triple": 3}

// newLine is ParaParser._new_line(k).
func (p *paraParser) newLine(kind int) bool {
	top := &p.stack[len(p.stack)-1]
	attr := top.line[kind]
	repeats, ok := lineRepeats[attr.kind]
	if attr.kind == "" {
		repeats, ok = 1, true
	}
	if !ok {
		return p.fail("KeyError %q", attr.kind)
	}
	entry := usLine{n: p.nlines, kind: kind, rise: top.rise, mult: repeats}
	defaults := p.style.lines[kind]
	entry.width, entry.offset, entry.gap = defaults.width, defaults.offset, defaults.gap
	if attr.width != nil {
		entry.width = *attr.width
	}
	if attr.offset != nil {
		entry.offset = *attr.offset
	}
	if attr.gap != nil {
		entry.gap = *attr.gap
	}
	if attr.hasColor {
		entry.hasColor, entry.color = true, attr.color
	} else if defaults.hasColor {
		entry.hasColor, entry.color = true, defaults.color
	}
	top.us = append(append([]usLine{}, top.us...), entry)
	p.nlines++
	return true
}

func (p *paraParser) startTag(tag string, attrs []htmlAttr) bool {
	switch tag {
	case "para":
		if len(attrs) > 0 {
			return p.unsupported()
		}
		p.started = true
		size := p.style.fontSize
		p.stack = []paraTag{{tag: "para", family: p.family, bold: strings.Contains(p.style.fontName, "Bold"),
			italic: strings.Contains(p.style.fontName, "Oblique"), size: size, color: p.style.textColor}}
		p.nlines, p.nlinks = 0, 0
		return true
	case "b", "strong":
		return p.push(tag, func(t *paraTag) { t.bold = true })
	case "i", "em":
		return p.push(tag, func(t *paraTag) { t.italic = true })
	case "br":
		return p.push(tag, func(t *paraTag) { t.selfClosing, t.lineBreak = "br", true })
	case "u", "strike":
		if len(p.stack) == 0 {
			return p.fail("tag outside the paragraph")
		}
		kind, specs := lineUnderline, underlineAttrs
		if tag == "strike" {
			kind, specs = lineStrike, strikeAttrs
		}
		var attr lineAttr
		if len(p.stack) > 0 {
			attr = p.stack[len(p.stack)-1].line[kind]
		}
		if !p.lineAttributes(attrs, specs, &attr) {
			return false
		}
		if !p.push(tag, func(t *paraTag) { t.line[kind] = attr }) {
			return false
		}
		return p.newLine(kind)
	}
	if paraNotPortedTags[tag] && !paraPorted[tag] {
		return p.unsupported()
	}
	return true
}

func (p *paraParser) endTag(tag string) bool {
	switch tag {
	case "para", "b", "strong", "i", "em":
		_, ok := p.pop(tag)
		return ok
	case "u", "strike":
		_, ok := p.pop(tag)
		return ok
	case "br":
		if len(p.stack) == 0 || p.stack[len(p.stack)-1].selfClosing != "br" {
			return p.fail("Parser failure in <br/>")
		}
		p.stack[len(p.stack)-1].selfClosing = ""
		if !p.data("") {
			return false
		}
		_, ok := p.pop("br")
		return ok
	}
	if paraNotPortedTags[tag] && !paraPorted[tag] {
		return p.unsupported()
	}
	return true
}

// fontName is tt2ps(family, bold, italic) of the Helvetica family.
func (t *paraTag) fontName() string {
	name := "Helvetica"
	switch {
	case t.bold && t.italic:
		name += "-BoldOblique"
	case t.bold:
		name += "-Bold"
	case t.italic:
		name += "-Oblique"
	}
	return name
}

func (p *paraParser) data(text string) bool {
	if len(p.stack) == 0 {
		return p.fail("text outside the paragraph")
	}
	top := p.stack[len(p.stack)-1]
	if top.img != nil {
		if text != "" {
			return p.fail("Only empty <img> tag allowed")
		}
	} else if top.selfClosing != "" {
		if text != "" {
			return p.fail("No content allowed in %s tag", top.selfClosing)
		}
		return true
	}
	back := top.back
	p.frags = append(p.frags, &frag{font: fontsByName[top.fontName()], size: top.size, color: top.color, rise: top.rise, text: text,
		lineBreak: top.lineBreak, us: top.us, links: top.links, back: back, nobr: top.nobr, img: top.img})
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
func parseParagraph(text string, style paraStyle) ([]*frag, paraStyle, error) {
	base := fontsByName[style.fontName]
	if !strings.ContainsAny(text, "<&") {
		if text == "" {
			return nil, style, nil
		}
		return []*frag{{font: base, size: style.fontSize, color: style.textColor, text: text}}, style, nil
	}
	if !style.linesSet {
		style.lines, style.linesSet = defaultLines(), true
	}
	parser := &paraParser{style: style, family: "Helvetica"}
	raw := []rune(text)
	if !paraIsPara(raw) {
		raw = []rune("<para>" + text + "</para>")
	}
	ok := (&htmlParser{raw: raw, events: parser}).feed()
	if parser.err != nil {
		return nil, style, parser.err
	}
	if !ok || !parser.started || len(parser.stack) > 0 {
		return nil, style, fmt.Errorf("%w: paragraph markup", ErrLayout)
	}
	return parser.frags, parser.style, nil
}
