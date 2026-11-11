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

// dim is the width or height of an image: points, or a percentage (of the
// paragraph width for a width, of the font size for a height).
type dim struct {
	v   float64
	pct bool
}

// norm is imgNormV: a percentage of nv, computed as _PCT.normalizedValue.
func (d dim) norm(nv float64) float64 {
	if d.pct {
		return (nv * d.v) * 0.01
	}
	return d.v
}

// valign is the vertical alignment of an image: a name, a percentage of the
// font size or a number of points.
type valign struct {
	name string
	pct  bool
	v    float64
}

// imgVRange is imgVRange: the bottom and top offsets from the baseline.
func imgVRange(h float64, va valign, fontSize float64) (float64, float64) {
	var iyo float64
	switch {
	case va.pct:
		iyo = (fontSize * va.v) * 0.01
	case va.name == "":
		iyo = va.v
	case va.name == "baseline":
	case va.name == "text-top" || va.name == "top":
		iyo = fontSize - h
	case va.name == "middle":
		iyo = fontSize - (1.2*fontSize+h)*0.5
	case va.name == "text-bottom" || va.name == "bottom":
		iyo = fontSize - 1.2*fontSize
	case va.name == "super":
		iyo = 0.5 * fontSize
	case va.name == "sub":
		iyo = -0.5 * fontSize
	}
	return iyo, iyo + h
}

// imgDefn is the cbDefn of an <img> fragment.
type imgDefn struct {
	src           string
	image         *jpegImage
	width, height dim
	valign        valign
}

// imgAttrs are the attributes of an <img> tag, inherited by the tags inside.
type imgAttrs struct {
	src           string
	width, height *dim
	valign        *valign
}

// paraPorted are the tags whose effect on the layout is reproduced. Every
// other tag ParaParser has a handler for is refused; tags it does not know
// are ignored, as it does.
var paraPorted = map[string]bool{"para": true, "b": true, "strong": true, "i": true, "em": true, "br": true, "u": true, "strike": true,
	"font": true, "span": true, "nobr": true, "sup": true, "super": true, "sub": true, "link": true, "a": true, "img": true}

// paraNotPortedTags are the tags of ParaParser that are still refused.
var paraNotPortedTags = map[string]bool{"bullet": true, "greek": true, "index": true,
	"link": true, "ondraw": true, "seq": true, "seqchain": true, "seqdefault": true, "seqformat": true,
	"seqreset": true, "unichar": true}

// lineAttr is what <u> and <strike> (and <link underline>) set on a frag.
type lineAttr struct {
	hasColor           bool
	color              color
	width, offset, gap *usVal
	kind               string
}

// supSpec is the rise or size string of <sup> and <sub>, inherited by the
// fragments inside.
type supSpec struct {
	raw string
	set bool
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
	supr, sups   supSpec
	nobr         bool
	imgAttrs     *imgAttrs
	selfClosing  string
	lineBreak    bool
	img          *imgDefn
}

// paraParser is reportlab's ParaParser.
type paraParser struct {
	style   paraStyle
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
		p.stack = []paraTag{{tag: "para", family: "helvetica", bold: strings.Contains(p.style.fontName, "Bold"),
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
	case "font", "span":
		return p.startFont(tag, attrs)
	case "nobr":
		if len(p.stack) == 0 {
			return p.fail("tag outside the paragraph")
		}
		if len(attrs) > 0 {
			return p.fail("invalid attribute name %s", attrs[0].name)
		}
		return p.push(tag, func(t *paraTag) { t.nobr = true })
	case "sup", "super", "sub":
		return p.startSup(tag, attrs)
	case "img":
		return p.startImg(attrs)
	case "link":
		return p.startLink(tag, attrs)
	case "a":
		for _, attr := range attrs {
			if attr.name == "name" {
				return p.startAnchor(attrs)
			}
		}
		return p.startLink(tag, attrs)
	}
	if paraNotPortedTags[tag] && !paraPorted[tag] {
		return p.unsupported()
	}
	return true
}

// startFont is start_font and start_span (the style attribute of a span
// raises: the parser does not know the styles).
func (p *paraParser) startFont(tag string, attrs []htmlAttr) bool {
	if len(p.stack) == 0 {
		return p.fail("tag outside the paragraph")
	}
	order, values := dedupeAttrs(attrs)
	var size *numVal
	var family string
	var bold, italic, hasFamily bool
	var textColor, backColor *color
	for _, name := range order {
		attr := values[name]
		switch name {
		case "size", "fontsize":
			if !attr.has {
				return p.fail("invalid size")
			}
			n, st := paraNum(attr.value, true)
			if st != attrOK {
				return p.attrFailure(st, "invalid size")
			}
			size = &n
		case "face", "name", "fontname":
			if !attr.has {
				return p.fail("invalid font name")
			}
			fam, b, i, st := ps2tt(attr.value)
			if st != attrOK {
				return p.attrFailure(st, "invalid font name")
			}
			family, bold, italic, hasFamily = fam, b, i, true
		case "fg", "color", "textcolor", "backcolor", "bgcolor":
			if !attr.has {
				return p.fail("invalid color")
			}
			c, st := paraColor(attr.value)
			if st != attrOK {
				return p.attrFailure(st, "invalid color")
			}
			if name == "backcolor" || name == "bgcolor" {
				backColor = &c
			} else {
				textColor = &c
			}
		case "style":
			if tag == "span" {
				return p.fail("findSpanStyle not implemented in this parser")
			}
		default:
			return p.fail("invalid attribute name %s", name)
		}
	}
	return p.push(tag, func(t *paraTag) {
		if size != nil {
			if size.rel {
				t.size += size.v
			} else {
				t.size = size.v
			}
		}
		if hasFamily {
			t.family, t.bold, t.italic = family, bold, italic
		}
		if textColor != nil {
			t.color = *textColor
		}
		if backColor != nil {
			t.back = backColor
		}
	})
}

// startSup is start_super and start_sub.
func (p *paraParser) startSup(tag string, attrs []htmlAttr) bool {
	if len(p.stack) == 0 {
		return p.fail("tag outside the paragraph")
	}
	order, values := dedupeAttrs(attrs)
	var rise, size supSpec
	for _, name := range order {
		attr := values[name]
		if name != "rise" && name != "size" {
			return p.fail("invalid attribute name %s", name)
		}
		if !attr.has {
			return p.fail("invalid value for %s", name)
		}
		if _, st := fontSizeNormalize(supSpec{attr.value, true}, p.stack[len(p.stack)-1].size, 0); st != attrOK {
			return p.attrFailure(st, "invalid value for "+name)
		}
		if name == "rise" {
			rise = supSpec{attr.value, true}
		} else {
			size = supSpec{attr.value, true}
		}
	}
	name := "super"
	if tag == "sub" {
		name = "sub"
	}
	if !p.push(name, func(t *paraTag) {
		if rise.set {
			t.supr = rise
		}
		if size.set {
			t.sups = size
		}
	}) {
		return false
	}
	top := &p.stack[len(p.stack)-1]
	fraction := 0.5
	delta, st := fontSizeNormalize(top.supr, top.size, top.size*fraction)
	if st != attrOK {
		return p.attrFailure(st, "invalid rise")
	}
	if name == "sub" {
		top.rise -= delta
	} else {
		top.rise += delta
	}
	newSize, st := fontSizeNormalize(top.sups, top.size, top.size-min(2, 0.2*top.size))
	if st != attrOK {
		return p.attrFailure(st, "invalid size")
	}
	top.size = newSize
	return true
}

func (p *paraParser) endTag(tag string) bool {
	switch tag {
	case "para", "b", "strong", "i", "em":
		_, ok := p.pop(tag)
		return ok
	case "u", "strike", "font", "span", "nobr":
		_, ok := p.pop(tag)
		return ok
	case "img":
		return p.endImg()
	case "link":
		_, ok := p.pop("link")
		return ok
	case "a":
		if len(p.stack) > 0 && p.stack[len(p.stack)-1].selfClosing != "" {
			return p.fail("Parser failure in <a/>")
		}
		_, ok := p.pop("a")
		return ok
	case "sup", "super":
		_, ok := p.pop("super")
		return ok
	case "sub":
		_, ok := p.pop("sub")
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

// fontName is tt2ps(family, bold, italic) of a family ps2tt or a link's
// name attribute gave; st says when the family is not known (an exception)
// or not ported (Courier and Times).
func (t *paraTag) fontName() (name string, st attrStatus) {
	family, bold, italic := strings.ToLower(t.family), t.bold, t.italic
	switch family {
	case "symbol", "zapfdingbats", "helvetica", "courier", "times":
	default:
		// tt2ps falls back to ps2tt for a concrete font name.
		fam, b, i, st := ps2tt(t.family)
		if st != attrOK {
			return "", st
		}
		family, bold, italic = fam, bold || b, italic || i
	}
	switch family {
	case "symbol":
		return "Symbol", attrOK
	case "zapfdingbats":
		return "ZapfDingbats", attrOK
	case "courier", "times":
		return "", attrUnsupported
	}
	name = "Helvetica"
	switch {
	case bold && italic:
		name += "-BoldOblique"
	case bold:
		name += "-Bold"
	case italic:
		name += "-Oblique"
	}
	return name, attrOK
}

// ps2tt is the mapping of reportlab.lib.fonts for the font names the markup
// can use: the family and the bold and italic flags. The Courier and Times
// families are not ported (their metrics are not in this package).
func ps2tt(name string) (family string, bold, italic bool, st attrStatus) {
	switch strings.ToLower(name) {
	case "helvetica":
		return "helvetica", false, false, attrOK
	case "helvetica-bold":
		return "helvetica", true, false, attrOK
	case "helvetica-boldoblique":
		return "helvetica", true, true, attrOK
	case "helvetica-oblique":
		return "helvetica", false, true, attrOK
	case "symbol":
		return "symbol", false, false, attrOK
	case "zapfdingbats":
		return "zapfdingbats", false, false, attrOK
	case "courier", "courier-bold", "courier-boldoblique", "courier-oblique", "times", "times-bold", "times-bolditalic", "times-italic",
		"times-roman":
		return "", false, false, attrUnsupported
	}
	return "", false, false, attrInvalid
}

// fontSizeNormalize is paraparser.fontSizeNormalize for a rise or size
// string of <sup> and <sub>.
func fontSizeNormalize(spec supSpec, size, def float64) (float64, attrStatus) {
	if !spec.set {
		return def, attrOK
	}
	raw := spec.raw
	if strings.HasSuffix(raw, "%") {
		v, st := paraConvNum(raw[:len(raw)-1], 1, true)
		if st != attrOK {
			return 0, st
		}
		if v.rel {
			return 0, attrInvalid // a tuple times 0.01
		}
		return (size * v.v) * 0.01, attrOK
	}
	v, st := paraNum(raw, true)
	if st != attrOK {
		return 0, st
	}
	if v.rel {
		return v.v + size, attrOK
	}
	return v.v, attrOK
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
	fontName, st := top.fontName()
	if st != attrOK {
		return p.attrFailure(st, "Can't find font family "+top.family)
	}
	back := top.back
	p.frags = append(p.frags, &frag{font: fontsByName[fontName], size: top.size, color: top.color, rise: top.rise, text: text,
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
	parser := &paraParser{style: style}
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

// startAnchor is the <a name="..."/> variant of start_a: the attribute errors
// of reportlab, then a refusal (the bookmark it makes is not ported).
func (p *paraParser) startAnchor(attrs []htmlAttr) bool {
	if len(p.stack) == 0 {
		return p.fail("tag outside the paragraph")
	}
	order, values := dedupeAttrs(attrs)
	for _, name := range order {
		if name != "name" {
			return p.fail("invalid attribute name %s", name)
		}
	}
	name := values["name"]
	if !name.has || pyTrimUnicode(name.value) == "" {
		return p.fail("<a name=\"...\"/> anchor variant requires non-blank name")
	}
	return p.unsupported()
}

// pyTrimUnicode is str.strip().
func pyTrimUnicode(s string) string { return strings.Trim(s, pySpaces) }

// linkTarget is what _doLink does with a link: no annotation for an empty
// one, a URI or GoToR annotation, or ok false for the destinations (#name,
// document:name) and the targets this package does not write.
func linkTarget(link string) (kind, target string, ok bool) {
	if link == "" {
		return "", "", true
	}
	for i := 0; i < len(link); i++ {
		if link[i] < 0x20 || link[i] >= 0x7f {
			return "", "", false
		}
	}
	if strings.HasPrefix(link, "#") {
		return "", "", false
	}
	scheme := ""
	if idx := strings.Index(link, ":"); idx >= 0 {
		scheme = strings.ToLower(link[:idx])
	}
	switch {
	case scheme == "document":
		return "", "", false
	case len(scheme) >= 2 && schemeLetters(scheme):
		if scheme == "pdf" {
			return "GoToR", link[strings.Index(link, ":")+1:], true
		}
	}
	return "URI", link, true
}

// schemeLetters is _scheme_re: a letter, then letters, digits, '-' and '+'.
func schemeLetters(s string) bool {
	for i := 0; i < len(s); i++ {
		c := s[i]
		letter := c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z'
		if i == 0 && !letter || !letter && !(c >= '0' && c <= '9') && c != '-' && c != '+' {
			return false
		}
	}
	return true
}

// startLink is _handle_link (<link> and <a href>).
func (p *paraParser) startLink(tag string, attrs []htmlAttr) bool {
	if len(p.stack) == 0 {
		return p.fail("tag outside the paragraph")
	}
	order, values := dedupeAttrs(attrs)
	var size *numVal
	var family string
	var hasFamily bool
	var textColor, backColor *color
	var href string
	var hasHref bool
	underline := false // platypus_link_underline
	var line lineAttr
	need := func(attr htmlAttr, what string) bool {
		if !attr.has {
			p.fail("invalid %s", what)
		}
		return attr.has
	}
	for _, name := range order {
		attr := values[name]
		switch name {
		case "size", "fontsize":
			if !need(attr, "size") {
				return false
			}
			n, st := paraNum(attr.value, true)
			if st != attrOK {
				return p.attrFailure(st, "invalid size")
			}
			size = &n
		case "face", "name", "fontname":
			if !need(attr, "font name") {
				return false
			}
			family, hasFamily = attr.value, true
		case "fg", "color", "textcolor", "backcolor", "bgcolor", "ucolor", "underlinecolor":
			if !need(attr, "color") {
				return false
			}
			c, st := paraColor(attr.value)
			if st != attrOK {
				return p.attrFailure(st, "invalid color")
			}
			switch name {
			case "backcolor", "bgcolor":
				backColor = &c
			case "ucolor", "underlinecolor":
				line.hasColor, line.color = true, c
			default:
				textColor = &c
			}
		case "dest", "destination", "target", "href", "link":
			if !need(attr, "link") {
				return false
			}
			href, hasHref = attr.value, true
		case "uoffset", "uwidth", "ugap", "underlineoffset", "underlinewidth", "underlinegap":
			v, ok := p.usValue(attr.value, attr.has)
			if !ok {
				return false
			}
			switch name {
			case "uoffset", "underlineoffset":
				line.offset = v
			case "uwidth", "underlinewidth":
				line.width = v
			default:
				line.gap = v
			}
		case "underline":
			if !need(attr, "underline") {
				return false
			}
			b, st := paraBool(attr.value)
			if st != attrOK {
				return p.attrFailure(st, "invalid underline")
			}
			underline = b
		case "ukind", "underlinekind":
			if !need(attr, "kind") {
				return false
			}
			line.kind = attr.value
		default:
			return p.fail("invalid attribute name %s", name)
		}
	}
	target := pyTrimUnicode(href)
	if hasHref {
		if _, _, ok := linkTarget(target); !ok {
			return p.unsupported()
		}
	}
	n := p.nlinks
	p.nlinks++
	top := p.stack[len(p.stack)-1]
	merged := top.line[lineUnderline]
	if line.hasColor {
		merged.hasColor, merged.color = true, line.color
	}
	if line.width != nil {
		merged.width = line.width
	}
	if line.offset != nil {
		merged.offset = line.offset
	}
	if line.gap != nil {
		merged.gap = line.gap
	}
	if line.kind != "" {
		merged.kind = line.kind
	}
	if !p.push(tag, func(t *paraTag) {
		t.links = append(append([]linkRef{}, t.links...), linkRef{n: n, target: target})
		t.line[lineUnderline] = merged
		if size != nil {
			if size.rel {
				t.size += size.v
			} else {
				t.size = size.v
			}
		}
		if hasFamily {
			t.family = family
		}
		if textColor != nil {
			t.color = *textColor
		}
		if backColor != nil {
			t.back = backColor
		}
	}) {
		return false
	}
	if underline {
		return p.newLine(lineUnderline)
	}
	return true
}

// numPct is _numpct(s, allowRelative=False): points or a percentage.
func numPct(s string) (dim, attrStatus) {
	if strings.HasSuffix(s, "%") {
		v, st := paraConvNum(s[:len(s)-1], 1, false)
		return dim{v: v.v, pct: true}, st
	}
	v, st := paraNum(s, false)
	return dim{v: v.v}, st
}

// valignPct is _valignpc.
func valignPct(s string) (valign, attrStatus) {
	if !asciiOnly(s) {
		return valign{}, attrUnsupported
	}
	lower := strings.ToLower(s)
	switch lower {
	case "baseline", "sub", "super", "top", "text-top", "middle", "bottom", "text-bottom":
		return valign{name: lower}, attrOK
	}
	if strings.HasSuffix(lower, "%") {
		n, st := paraConvNum(lower[:len(lower)-1], 1, true)
		return valign{pct: true, v: n.v}, st
	}
	n, st := paraNum(s, true)
	return valign{v: n.v}, st
}

// startImg is start_img.
func (p *paraParser) startImg(attrs []htmlAttr) bool {
	if len(p.stack) == 0 {
		return p.fail("tag outside the paragraph")
	}
	order, values := dedupeAttrs(attrs)
	img := &imgAttrs{}
	have := map[string]bool{}
	for _, name := range order {
		attr := values[name]
		switch name {
		case "src":
			if attr.has {
				img.src = attr.value
			}
			have["src"] = attr.has
		case "width", "height":
			if !attr.has {
				return p.fail("invalid %s", name)
			}
			d, st := numPct(attr.value)
			if st != attrOK {
				return p.attrFailure(st, "invalid "+name)
			}
			if name == "width" {
				img.width = &d
			} else {
				img.height = &d
			}
		case "valign":
			if !attr.has {
				return p.fail("invalid valign")
			}
			v, st := valignPct(attr.value)
			if st != attrOK {
				return p.attrFailure(st, "invalid valign")
			}
			img.valign = &v
		default:
			return p.fail("invalid attribute name %s", name)
		}
	}
	if img.src == "" {
		return p.fail("<img> needs src attribute")
	}
	inherited := p.stack[len(p.stack)-1].imgAttrs
	if inherited != nil {
		if img.width == nil {
			img.width = inherited.width
		}
		if img.height == nil {
			img.height = inherited.height
		}
		if img.valign == nil {
			img.valign = inherited.valign
		}
	}
	return p.push("img", func(t *paraTag) { t.imgAttrs, t.selfClosing = img, "img" })
}

// loadDataImage is ImageReader(src) for a data: URL with base64 data: the
// image, an exception of reportlab (ok false), or a refusal (files, URLs,
// other encodings and formats).
func loadDataImage(src string) (image *jpegImage, st attrStatus) {
	if len(src) < 5 || !strings.EqualFold(src[:5], "data:") {
		return nil, attrUnsupported
	}
	typ, data, found := strings.Cut(src, ",")
	if !found {
		return nil, attrInvalid
	}
	encoding := ""
	if semi := strings.LastIndex(typ, ";"); semi >= 0 && !strings.Contains(typ[semi:], "=") {
		encoding = typ[semi+1:]
	}
	if encoding != "base64" {
		return nil, attrUnsupported
	}
	if !asciiOnly(data) {
		return nil, attrInvalid
	}
	raw, ok := pythonB64Decode(data)
	if !ok || len(raw) == 0 {
		return nil, attrInvalid
	}
	if len(raw) < 3 || raw[0] != 0xFF || raw[1] != 0xD8 || raw[2] != 0xFF {
		return nil, attrUnsupported
	}
	image, err := loadJPEG(data)
	if err != nil || image == nil {
		return nil, attrUnsupported
	}
	return image, attrOK
}

// endImg is end_img.
func (p *paraParser) endImg() bool {
	if len(p.stack) == 0 || p.stack[len(p.stack)-1].selfClosing != "img" {
		return p.fail("Parser failure in <img/>")
	}
	top := &p.stack[len(p.stack)-1]
	image, st := loadDataImage(top.imgAttrs.src)
	if st != attrOK {
		return p.attrFailure(st, "Cannot open resource")
	}
	defn := &imgDefn{src: top.imgAttrs.src, image: image, width: dim{v: float64(image.width)}, height: dim{v: float64(image.height)},
		valign: valign{name: "bottom"}}
	if a := top.imgAttrs; a != nil {
		if a.width != nil {
			defn.width = *a.width
		}
		if a.height != nil {
			defn.height = *a.height
		}
		if a.valign != nil {
			defn.valign = *a.valign
		}
	}
	top.img = defn
	top.selfClosing = ""
	if !p.data("") {
		return false
	}
	_, ok := p.pop("img")
	return ok
}
