package pdfexport

import (
	"math"
	"strings"
	"unicode"
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
	// wordWrap is 'CJK', 'RTL', 'LTR' or '' (the style's value).
	wordWrap string
	// hyphenationLang is the style's language tag. pyphen is not installed, so
	// a tag leaves reportlab without a hyphenator; only the style keeps it.
	hyphenationLang string
	// uriWasteReduce is the fraction of a line the split of a URI may waste,
	// embeddedHyphenation splits words that have hyphens of their own and
	// hyphenationOverflow is an attribute no layout reads.
	uriWasteReduce      pyNum
	embeddedHyphenation bool
	hyphenationOverflow bool
	// minWordLength is hyphenationMinWordLength when minWordLengthSet, else 5
	// (rl_settings.hyphenationMinWordLength).
	minWordLength    pyNum
	minWordLengthSet bool
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

// hyphenationMinWordLength is getattr(style,'hyphenationMinWordLength',
// hyphenationMinWordLength): the style's value, else rl_settings' 5.
func (s paraStyle) hyphenationMinWordLength() pyNum {
	if s.minWordLengthSet {
		return s.minWordLength
	}
	return numInt(5)
}

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
// a long word (the last one a _SplitWordEnd), a _SplitWordH head that
// hyphenation made, or a _SHYStr with the places of its soft hyphens.
type simpleWord struct {
	text  []rune
	split bool
	end   bool
	head  bool
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
	// hyphenations is _hyphenations: the words breakLines split.
	hyphenations int
	// keptLines and keptFragLines are the lines of a paragraph of wordWrap
	// 'CJK' that a split made, which breakLinesCJK answers instead of breaking
	// the words again.
	keptLines     []simpleLine
	keptFragLines []*fragLine
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

// --- hyphenation -----------------------------------------------------------------------------------------------

// hyAttempt is the hyphenation state of Paragraph.breakLines: reportlab's
// attemptHyphenation, uriWasteReduce, embeddedHyphenation, hyphenation2 and
// hymwl. pyphen is not installed, so a hyphenationLang gives no hyphenator and
// only the URI and embedded-hyphen branches split a word.
type hyAttempt struct {
	enabled  bool
	uriWaste pyNum
	embedded bool
	// second is hyphenation2: embeddedHyphenation>1, which the boolean a style
	// keeps never is, so the branches it guards never answer from markup.
	second     bool
	minWordLen pyNum
}

// hyphenation is the hyphenation state of Paragraph.breakLines.
func (p *paragraph) hyphenation() hyAttempt {
	s := p.style
	return hyAttempt{enabled: s.uriWasteReduce.truthy() || s.embeddedHyphenation, uriWaste: s.uriWasteReduce,
		embedded: s.embeddedHyphenation, minWordLen: s.hyphenationMinWordLength()}
}

// forNobr is the state breakLines passes for a fragment of <nobr>: the
// hyphenator and the URI reduction are off, the embedded hyphenation too.
func (h hyAttempt) forNobr(nobr bool) hyAttempt {
	if !nobr {
		return h
	}
	h.uriWaste = numInt(0)
	h.embedded = false
	return h
}

// hyUriWaste is `w0 <= (1-uriWasteReduce)*maxWidth` of _hyGenPair.
func hyUriWaste(reduce pyNum, w0, maxWidth float64) bool {
	limit, ok := numMul(numSubOrErr(numInt(1), reduce), numFloat(maxWidth))
	if !ok {
		panic(paraFailure{"int too large to convert to float"})
	}
	w := numFloat(w0)
	return numLess(w, limit) || numEqual(w, limit)
}

const (
	// hyPfxRunes is the class of _hy_pfx_pat.
	hyPfxRunes = "'\"([{\u00bf\u2018\u201a\u201c\u201e"
	// hySfxRunes is the class of _hy_sfx_pat.
	hySfxRunes = "]'\")}?!.,;:\u2019\u201b\u201d\u201f"
)

// hyLetter is a member of _hy_letters: the Latin letters the hyphenation of
// reportlab considers, with the Cyrillic range.
func hyLetter(r rune) bool {
	switch {
	case r >= 'A' && r <= 'Z', r >= 'a' && r <= 'z':
		return true
	case r >= 0xC0 && r <= 0xD6, r >= 0xD8 && r <= 0xF6, r >= 0xF8 && r <= 0x24F:
		return true
	case r >= 0x1E00 && r <= 0x1EFF, r >= 0x410 && r <= 0x44F:
		return true
	}
	return false
}

// hyShyLettersAll is _hy_shy_letters_pat: a non-empty run of _hy_letters, '-'
// and soft hyphens.
func hyShyLettersAll(s []rune) bool {
	if len(s) == 0 {
		return false
	}
	for _, r := range s {
		if r != '-' && r != softHyphen && !hyLetter(r) {
			return false
		}
	}
	return true
}

// hyShySplit is _hy_shy_pat.split: the runs of a word between its hyphens and
// soft hyphens, with the separators between them.
func hyShySplit(s []rune) [][]rune {
	parts := [][]rune{}
	start := 0
	for i, r := range s {
		if r == '-' || r == softHyphen {
			parts = append(parts, s[start:i], s[i:i+1])
			start = i + 1
		}
	}
	return append(parts, s[start:])
}

// hySplitWord is the prefix, suffix and middle of _hyGenPair: the quotes and
// brackets a word starts with, the punctuation it ends with, and the rest.
func hySplitWord(s []rune) (pfx, middle, sfx []rune) {
	p := 0
	for p < len(s) && strings.ContainsRune(hyPfxRunes, s[p]) {
		p++
	}
	e := len(s)
	for e > p && strings.ContainsRune(hySfxRunes, s[e-1]) {
		e--
	}
	return s[:p], s[p:e], s[e:]
}

// --- URIs ------------------------------------------------------------------------------------------------------

// uriSchemeRune is a member of the scheme class of uri_pat under re.I.
func uriSchemeRune(r rune) bool {
	switch {
	case r >= 'A' && r <= 'Z', r >= 'a' && r <= 'z', r >= '0' && r <= '9':
		return true
	case r == '.', r == '-', r == '+':
		return true
	case r == 0x130, r == 0x131, r == 0x17F, r == 0x212A: // İ ı ſ K, which re.I folds to [a-z]
		return true
	}
	return false
}

// uriAlnum is [a-z\xa1-\uffff0-9] of uri_pat under re.I.
func uriAlnum(r rune) bool {
	return r >= 'A' && r <= 'Z' || r >= 'a' && r <= 'z' || r >= '0' && r <= '9' || r >= 0xA1 && r <= 0xFFFF
}

// uriAlnumHyphen is [a-z\xa1-\uffff0-9-] of uri_pat under re.I.
func uriAlnumHyphen(r rune) bool { return uriAlnum(r) || r == '-' }

// uriTLDRune is [a-z\xa1-\uffff] of uri_pat under re.I.
func uriTLDRune(r rune) bool {
	return r >= 'A' && r <= 'Z' || r >= 'a' && r <= 'z' || r >= 0xA1 && r <= 0xFFFF
}

// uriTLDHyphenRune is [a-z\xa1-\uffff-] of uri_pat under re.I.
func uriTLDHyphenRune(r rune) bool { return uriTLDRune(r) || r == '-' }

// uriSchemeDigit is [a-z0-9] of the xn-- branch under re.I.
func uriSchemeDigit(r rune) bool {
	return r >= 'A' && r <= 'Z' || r >= 'a' && r <= 'z' || r >= '0' && r <= '9' ||
		r == 0x130 || r == 0x131 || r == 0x17F || r == 0x212A
}

// uriHex is [0-9a-f:.] under re.I.
func uriHex(r rune) bool {
	return r >= '0' && r <= '9' || r >= 'a' && r <= 'f' || r >= 'A' && r <= 'F' || r == ':' || r == '.'
}

// uriSpace is \s of a Python pattern on str: the characters str.isspace
// answers true for.
func uriSpace(r rune) bool { return strings.ContainsRune(pySpaces, r) }

// uriNonSpace is \S of a Python pattern on str.
func uriNonSpace(r []rune) bool {
	for _, r := range r {
		if uriSpace(r) {
			return false
		}
	}
	return true
}

// uriDigit is \d of a Python pattern on str, which is \p{Nd}.
func uriDigit(r rune) bool { return unicode.Is(unicode.Nd, r) }

// uriTLDEnd is the end of the top-level domain that starts at i of uri_pat
// (`(?!-)(?:[a-z\xa1-\uffff-]{2,63}|xn--[a-z0-9]{1,59})(?<!-)`), or -1.
func uriTLDEnd(r []rune, i int) int {
	if i >= len(r) || !uriTLDRune(r[i]) {
		return -1
	}
	j := i
	for j < len(r) && uriTLDHyphenRune(r[j]) {
		j++
	}
	if j-i >= 2 && j-i <= 63 && r[j-1] != '-' {
		return j
	}
	if j-i >= 5 && strings.EqualFold(string(r[i:i+4]), "xn--") {
		k := i + 4
		for k < len(r) && uriSchemeDigit(r[k]) {
			k++
		}
		if n := k - i - 4; n >= 1 && n <= 59 {
			return k
		}
	}
	return -1
}

// uriTail is `(?::\d{2,5})?(?:[/?#][^\s]*)?\Z` of uri_pat from i.
func uriTail(r []rune, i int) bool {
	if i == len(r) {
		return true
	}
	switch r[i] {
	case '/', '?', '#':
		return uriNonSpace(r[i:])
	case ':':
		j := i + 1
		for j < len(r) && uriDigit(r[j]) {
			j++
		}
		if digits := j - i - 1; digits < 2 || digits > 5 {
			return false
		}
		if j == len(r) {
			return true
		}
		if r[j] == '/' || r[j] == '?' || r[j] == '#' {
			return uriNonSpace(r[j:])
		}
	}
	return false
}

// uriIPv4 is the IPv4 branch of uri_pat from i: four octets and the tail.
func uriIPv4(r []rune, i, octet int) bool {
	if octet == 3 {
		for _, end := range uriOctetEnds(r, i) {
			if uriTail(r, end) {
				return true
			}
		}
		return false
	}
	for _, end := range uriOctetEnds(r, i) {
		if end < len(r) && r[end] == '.' && uriIPv4(r, end+1, octet+1) {
			return true
		}
	}
	return false
}

// uriOctetEnds is `(25[0-5]|2[0-4]\d|[0-1]?\d?\d)` of uri_pat at i: the ends
// the alternatives can take.
func uriOctetEnds(r []rune, i int) []int {
	var ends []int
	if i+3 <= len(r) {
		if r[i] == '2' && r[i+1] == '5' && r[i+2] >= '0' && r[i+2] <= '5' {
			ends = append(ends, i+3)
		}
		if r[i] == '2' && r[i+1] >= '0' && r[i+1] <= '4' && uriDigit(r[i+2]) {
			ends = append(ends, i+3)
		}
		if r[i] >= '0' && r[i] <= '1' && uriDigit(r[i+1]) && uriDigit(r[i+2]) {
			ends = append(ends, i+3)
		}
	}
	if i+2 <= len(r) && uriDigit(r[i]) && uriDigit(r[i+1]) {
		ends = append(ends, i+2)
	}
	if i < len(r) && uriDigit(r[i]) {
		ends = append(ends, i+1)
	}
	return ends
}

// uriDomain is the domain branch of uri_pat at the start of r: a label, some
// labels after dots, a top-level domain and an optional dot.
func uriDomain(r []rune) bool {
	var runs [][2]int
	i := 0
	for i < len(r) {
		j := i
		for j < len(r) && uriAlnumHyphen(r[j]) {
			j++
		}
		runs = append(runs, [2]int{i, j})
		if j >= len(r) || r[j] != '.' {
			break
		}
		i = j + 1
	}
	// The domain is a label, some labels after dots and a top-level domain
	// after a dot, so the top-level domain is the second run or a later one.
	for j := len(runs) - 1; j >= 1; j-- {
		ok := true
		for k := 0; k < j; k++ {
			start, end := runs[k][0], runs[k][1]
			if end == start || end-start > 63 || !uriAlnum(r[start]) || !uriAlnum(r[end-1]) {
				ok = false
				break
			}
		}
		if !ok {
			continue
		}
		end := uriTLDEnd(r, runs[j][0])
		if end < 0 {
			continue
		}
		if end == len(r) {
			return true
		}
		if r[end] == '.' && (end+1 == len(r) || uriTail(r, end+1)) {
			return true
		}
		if uriTail(r, end) {
			return true
		}
	}
	return false
}

// uriHost is the host branches of uri_pat from the start of rest.
func uriHost(rest []rune) bool {
	if len(rest) > 0 && rest[0] == '[' {
		j := 1
		for j < len(rest) && uriHex(rest[j]) {
			j++
		}
		if j > 1 && j < len(rest) && rest[j] == ']' && uriTail(rest, j+1) {
			return true
		}
	}
	if uriIPv4(rest, 0, 0) {
		return true
	}
	if uriDomain(rest) {
		return true
	}
	if localhost := "localhost"; strings.HasPrefix(string(rest), localhost) && uriTail(rest, len(localhost)) {
		return true
	}
	return false
}

// uriMatch is uri_pat.match: the scheme (which ends in '://') and the rest, or
// ok false.
func uriMatch(s []rune) ([]rune, []rune, bool) {
	n := 0
	for n < len(s) && uriSchemeRune(s[n]) {
		n++
	}
	if n+3 > len(s) || s[n] != ':' || s[n+1] != '/' || s[n+2] != '/' {
		return nil, nil, false
	}
	scheme, rest := s[:n+3], s[n+3:]
	// `(?:\S+(?::\S*)?@)?`: any non-empty non-space prefix ending at an '@'.
	for i := len(rest) - 1; i >= 1; i-- {
		if rest[i] == '@' && uriNonSpace(rest[:i]) && uriHost(rest[i+1:]) {
			return scheme, rest, true
		}
	}
	if uriHost(rest) {
		return scheme, rest, true
	}
	return nil, nil, false
}

// uriSplitPairs is _uri_split_pairs: the heads and tails a URI can be split
// into at its slashes, longest head first, or nil.
func uriSplitPairs(s []rune) [][2][]rune {
	scheme, rest, ok := uriMatch(s)
	if !ok {
		return nil
	}
	var pairs [][2][]rune
	if len(scheme) > 0 && len(rest) > 0 {
		pairs = append(pairs, [2][]rune{scheme, rest})
	}
	pairs = append(pairs, uriSlashParts(rest, scheme)...)
	for i, j := 0, len(pairs)-1; i < j; i, j = i+1, j-1 {
		pairs[i], pairs[j] = pairs[j], pairs[i]
	}
	return pairs
}

// uriSlashParts is _slash_parts with '/' as its slash: a URI split after each
// of its slashes, the trailing slashes of the URI kept on the tails.
func uriSlashParts(uri, scheme []rune) [][2][]rune {
	tail := []rune{}
	for len(uri) > 0 && uri[len(uri)-1] == '/' {
		tail = append(tail, '/')
		uri = uri[:len(uri)-1]
	}
	var pairs [][2][]rune
	i := 2
	for {
		found := -1
		for k := i; k < len(uri); k++ {
			if uri[k] == '/' {
				found = k
				break
			}
		}
		if found < 0 {
			break
		}
		i = found + 1
		head := append(append([]rune{}, scheme...), uri[:i]...)
		rest := append(append([]rune{}, uri[i:]...), tail...)
		pairs = append(pairs, [2][]rune{head, rest})
	}
	return pairs
}

// hyPair is what _hyGenPair answers: the head and the tail of a split word and
// their widths. The jc hyphen a hyphenator adds is always empty here, because
// pyphen is not installed.
type hyPair struct {
	head, tail []rune
	headWidth  float64
	tailWidth  float64
}

// hyGenPair is _hyGenPair: the head and tail of a word split at a URI slash or,
// with embeddedHyphenation, at one of its own hyphens. hyphenator is nil
// (pyphen is not installed), so a word outside those two cases is not split.
func hyGenPair(h hyAttempt, word []rune, ww, newWidth, maxWidth float64, f *font, size float64) *hyPair {
	pfx, s, sfx := hySplitWord(word)
	if numLess(numInt(int64(len(s))), h.minWordLen) {
		return nil
	}
	w0 := newWidth - ww
	if pairs := uriSplitPairs(s); pairs != nil {
		if ww > maxWidth || (h.uriWaste.truthy() && hyUriWaste(h.uriWaste, w0, maxWidth)) {
			for _, pair := range pairs {
				head := append(append([]rune{}, pfx...), pair[0]...)
				tail := append(append([]rune{}, pair[1]...), sfx...)
				hw := stringWidth(string(head), f, size)
				if w0+hw <= maxWidth {
					return &hyPair{head: head, tail: tail, headWidth: hw, tailWidth: ww - hw}
				}
			}
		}
		return nil
	}
	H := hyShySplit(s)
	n := len(H)
	if n >= 3 && h.embedded && !hyHasEmpty(H) && hyShyLettersAll(s) {
		for i := n - 1; i >= 2; i-- {
			if i%2 != 0 {
				continue
			}
			head := append(append([]rune{}, pfx...), hyJoin(H[:i])...)
			tail := append(hyJoin(H[i:]), sfx...)
			hw := stringWidth(string(head), f, size)
			if w0+hw <= maxWidth {
				return &hyPair{head: head, tail: tail, headWidth: hw, tailWidth: ww - hw}
			}
		}
	}
	return nil
}

// hyHasEmpty reports an empty piece of H in _hyGenPair.
func hyHasEmpty(parts [][]rune) bool {
	for _, part := range parts {
		if len(part) == 0 {
			return true
		}
	}
	return false
}

// hyJoin concatenates the parts of a split word.
func hyJoin(parts [][]rune) []rune {
	var out []rune
	for _, part := range parts {
		out = append(out, part...)
	}
	return out
}

// hyphenateWord is _hyphenateWord: the head and tail of a word of the one-font
// layout, or nil.
func (p *paragraph) hyphenateWord(h hyAttempt, f *font, size float64, word []rune, ww, newWidth, maxWidth float64) []*simpleWord {
	if ww == 0 {
		return nil
	}
	pair := hyGenPair(h, word, ww, newWidth, maxWidth, f, size)
	if pair == nil {
		return nil
	}
	return []*simpleWord{{text: pair.head, split: true, head: true}, {text: pair.tail, split: true, end: true}}
}

// fragWordPos is one entry of _fragWordSplitRep: the fragment and the offset in
// it of a character of the word the fragments assemble.
type fragWordPos struct {
	part, offset int
}

// fragWordSplitRep is _fragWordSplitRep: the word the fragments of a frag word
// assemble with the position of each of its characters, ok false for a rise or
// an image.
func fragWordSplitRep(w *fragWord) ([]rune, []fragWordPos, bool) {
	var u []rune
	var x []fragWordPos
	for i, part := range w.parts {
		if !numEqual(part.f.rise, numInt(0)) {
			return nil, nil, false
		}
		if part.f.img != nil && part.f.img.width.truthy() {
			return nil, nil, false
		}
		if len(part.text.s) == 0 {
			continue
		}
		offset := len(u)
		for _, r := range part.text.s {
			x = append(x, fragWordPos{i + 1, offset})
			u = append(u, r)
		}
	}
	return u, x, true
}

// rebuildFragWord is _rebuildFragWord: the frag word of some parts with its
// width.
func rebuildFragWord(parts []fragPart) *fragWord {
	total := 0.0
	for _, part := range parts {
		total += fragWidth(part.text.s, part.f)
	}
	return &fragWord{width: total, parts: parts}
}

// fragPartOf is FW[i]: the fragment and text of a part.
func fragPartOf(w *fragWord, i int) fragPart {
	if i < 0 || i >= len(w.parts) {
		panic(paraFailure{"list index out of range"})
	}
	return w.parts[i]
}

// hyGenFragsPair is _hyGenFragsPair: the head and tail frag words a word of
// several fragments is split into, or nil.
func hyGenFragsPair(h hyAttempt, w *fragWord, newWidth, maxWidth float64) (*fragWord, *fragWord) {
	u, x, ok := fragWordSplitRep(w)
	if !ok {
		return nil, nil
	}
	pfx, s, _ := hySplitWord(u)
	if numLess(numInt(int64(len(s))), h.minWordLen) {
		return nil, nil
	}
	ww := w.width
	w0 := newWidth - ww

	// splitAt cuts the fragments at the character pos of the assembled word.
	splitAt := func(head []rune) (*fragWord, *fragWord) {
		pos := len(head)
		if pos >= len(x) {
			panic(paraFailure{"tuple index out of range"})
		}
		fx, cc := x[pos].part, x[pos].offset
		part := fragPartOf(w, fx-1)
		var left []fragPart
		left = append(left, w.parts[:fx-1]...)
		if text := part.text.s[:pos-cc]; len(text) > 0 {
			left = append(left, fragPart{part.f, shyText{s: text}})
		}
		var right []fragPart
		if text := part.text.s[pos-cc:]; len(text) > 0 {
			right = append(right, fragPart{part.f, shyText{s: text}})
		}
		right = append(right, w.parts[fx:]...)
		return rebuildFragWord(left), rebuildFragWord(right)
	}

	if pairs := uriSplitPairs(s); pairs != nil {
		if ww > maxWidth || (h.uriWaste.truthy() && hyUriWaste(h.uriWaste, w0, maxWidth)) {
			for _, pair := range pairs {
				head := append(append([]rune{}, pfx...), pair[0]...)
				hl, tl := splitAt(head)
				if w0+hl.width <= maxWidth {
					return hl, tl
				}
			}
		}
		return nil, nil
	}
	H := hyShySplit(s)
	n := len(H)
	if n >= 3 && h.embedded && !hyHasEmpty(H) && hyShyLettersAll(s) {
		for i := n - 1; i >= 2; i-- {
			if i%2 != 0 {
				continue
			}
			head := append(append([]rune{}, pfx...), hyJoin(H[:i])...)
			hl, tl := splitAt(head)
			if w0+hl.width <= maxWidth {
				return hl, tl
			}
		}
	}
	return nil, nil
}

// hyphenateFragWord is _hyphenateFragWord: the head and tail frag words of a
// word of the several-fragment layout, or nil.
func (p *paragraph) hyphenateFragWord(h hyAttempt, w *fragWord, newWidth, maxWidth float64) []*fragWord {
	if w.width == 0 {
		return nil
	}
	if len(w.parts) == 1 {
		part := w.parts[0]
		pair := hyGenPair(h, part.text.s, w.width, newWidth, maxWidth, part.f.font, part.f.size.float())
		if pair == nil {
			return nil
		}
		head := &fragWord{width: pair.headWidth, split: true, splitH: true}
		head.parts = []fragPart{{part.f, shyText{s: pair.head}}}
		tail := &fragWord{width: pair.tailWidth, split: true, hs: w.hs}
		tail.parts = []fragPart{{part.f, shyText{s: pair.tail}}}
		return []*fragWord{head, tail}
	}
	head, tail := hyGenFragsPair(h, w, newWidth, maxWidth)
	if head == nil {
		return nil
	}
	head.split, head.splitH = true, true
	tail.split, tail.hs = true, w.hs
	return []*fragWord{head, tail}
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
	hy := p.hyphenation()
	nobrHy := hy.forNobr(f.nobr)
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
		if newWidth > limWidth && !word.head && !forced {
			if word.shy {
				if parts := word.shySplit(f.font, size, currentWidth+spaceWidth+hyphenWidth-1e-8, limWidth); parts != nil {
					words = append(parts, words...)
					p.hyphenations++
					forced = true
					continue
				} else if len(line) > 0 {
					nMW := maxWidths[min(maxlineno, lineno)]
					if hy.second || word.fsww+hyphenWidth+1e-8 <= nMW {
						if word.shySplit(f.font, size, hyphenWidth-1e-8, nMW) != nil {
							words = append([]*simpleWord{word}, words...)
							forced, dropped = true, true
							newWidth = currentWidth
						}
					}
				}
			} else if hy.enabled {
				if parts := p.hyphenateWord(nobrHy, f.font, size, word.text, wordWidth, newWidth, limWidth); parts != nil {
					words = append(parts, words...)
					p.hyphenations++
					forced = true
					continue
				} else if hy.second && len(line) > 0 {
					if p.hyphenateWord(nobrHy, f.font, size, word.text, wordWidth, wordWidth, maxWidth) != nil {
						words = append([]*simpleWord{word}, words...)
						forced, dropped = true, true
						newWidth = currentWidth
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
	hy := p.hyphenation()
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
					p.hyphenations++
					continue
				} else if len(done) > 1 && w.fsww+1e-8 <= maxWidth {
					if w.shyphenate(wordWidth, maxWidth) != nil {
						pending = append([]*fragWord{injected(f), w}, pending...)
						done = done[:len(done)-1]
						continue
					}
				}
			} else if hy.enabled {
				if cut := p.hyphenateFragWord(hy.forNobr(f.nobr), w, newWidth, limWidth); cut != nil {
					pending = append([]*fragWord{cut[0], injected(f), cut[1]}, pending...)
					done = done[:len(done)-1]
					p.hyphenations++
					continue
				} else if hy.second && len(done) > 1 {
					if p.hyphenateFragWord(hy.forNobr(f.nobr), w, wordWidth, maxWidth) != nil {
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

// --- CJK line breaking -----------------------------------------------------------------------------------------

// allCannotStart is textsplit.ALL_CANNOT_START: the characters Japanese
// typography does not start a line with.
const allCannotStart = "!',.:;?!\")]\u3001\u3002\u300d\u300f\u3011\u3015\uff3d\u3011\uff09" +
	"\u3005\u2015\u3041\u3043\u3045\u3047\u3049\u3063\u3083\u3085\u3087\u308e\u30a1\u30a3" +
	"\u30a5\u30a7\u30a9\u30c3\u30e3\u30e5\u30e7\u30ee\u30fc\u30f5\u30f6" +
	"\u309b\u309c\u30fb\u30fd\u30fe\u309d\u309e\u2015\u2010\u00b0\u2032\u2033\u2103\uffe0\uff05\u2030"

// cjkUnit is cjkU: one character of a fragment (or the empty text of a
// fragment without characters), with its width.
type cjkUnit struct {
	text     string
	f        *frag
	width    float64
	pct      bool
	pctValue pyNum
}

// cjkUnitOf is cjkU(text, f, encoding): a character's width, or the width of
// the image a fragment draws.
func cjkUnitOf(text string, f *frag) cjkUnit {
	if f.img != nil {
		return cjkUnit{text: text, f: f, width: f.img.width.norm(numFloat(1)), pct: f.img.width.pct, pctValue: f.img.width.v}
	}
	if f.anchor != nil {
		return cjkUnit{text: text, f: f}
	}
	return cjkUnit{text: text, f: f, width: stringWidth(text, f.font, f.size.float())}
}

// cjkWidth is u.width with the percentage of a cbDefn normalized to maxWidth.
func (u cjkUnit) cjkWidth(maxWidth float64) float64 {
	if u.pct {
		return u.pctValue.float() * 0.01 * maxWidth
	}
	return u.width
}

// wordSplit is textsplit.wordSplit: the lines a text without spaces is cut
// into.
func wordSplit(text string, maxWidths []float64, f *font, size float64) []simpleLine {
	word := []rune(text)
	widths := make([]float64, len(word))
	for i, r := range word {
		widths[i] = stringWidth(string(r), f, size)
	}
	return dumbSplit(word, widths, maxWidths)
}

// dumbSplit is textsplit.dumbSplit: as many characters as fit on a line, cut
// between characters, with the characters Japanese typography may not start a
// line kept on the line before.
func dumbSplit(word []rune, widths, maxWidths []float64) []simpleLine {
	var lines []simpleLine
	i, lineStartPos := 0, 0
	widthUsed := 0.0
	maxWidth := maxWidths[0]
	nW := len(word)
	for i < nW {
		w := widths[i]
		c := word[i]
		widthUsed += w
		i++
		if widthUsed > maxWidth+fuzz && widthUsed > 0 {
			extraSpace := maxWidth - widthUsed
			if c < 0x3000 {
				// A section of a non-Asian script: look back for a space or a
				// return to Kanji, or half way.
				limitCheck := (lineStartPos + i) >> 1
				for j := i - 1; j > limitCheck; j-- {
					cj := word[j]
					if unicode.Is(unicode.Zs, cj) || cj >= 0x3000 {
						k := j + 1
						if k < i {
							j = k + 1
							for ii := j; ii < i; ii++ {
								extraSpace += widths[ii]
							}
							w = widths[k]
							c = word[k]
							i = j
							break
						}
					}
				}
			}
			if !strings.ContainsRune(allCannotStart, c) && i > lineStartPos+1 {
				i--
				extraSpace += w
			}
			lines = append(lines, simpleLine{extra: extraSpace, words: []*simpleWord{{text: []rune(strings.Trim(string(word[lineStartPos:i]), pySpaces))}}})
			if len(lines) < len(maxWidths) {
				maxWidth = maxWidths[len(lines)]
			} else {
				maxWidth = maxWidths[len(maxWidths)-1]
			}
			lineStartPos = i
			widthUsed = 0
		}
	}
	if widthUsed > 0 {
		lines = append(lines, simpleLine{extra: maxWidth - widthUsed, words: []*simpleWord{{text: append([]rune{}, word[lineStartPos:]...)}}})
	}
	return lines
}

// makeCJKParaLine is makeCJKParaLine: the fragments of the characters of one
// line, joined where they are the same fragment.
func makeCJKParaLine(U []cjkUnit, maxWidth, widthUsed, extraSpace float64, lineBreak, calcBounds bool) *fragLine {
	var words []*frag
	var current []rune
	var previous *frag
	maxSize, maxAscent, minDescent := pyNum{}, 0.0, 0.0
	flush := func() {
		if previous == nil {
			return
		}
		g := previous.clone()
		g.text = string(current)
		words = append(words, g)
		current = nil
	}
	for _, u := range U {
		f := u.f
		size := f.size
		var ascent, descent float64
		if calcBounds && f.img != nil && f.img.width.truthy() {
			h := f.img.height.norm(size)
			descent, ascent = imgVRange(h, f.img.valign, size)
		} else {
			ascent, descent = metrics(f.font, size.float())
		}
		maxSize = numMax(maxSize, size)
		maxAscent = pyMax(maxAscent, ascent)
		minDescent = pyMin(minDescent, descent)
		if previous == nil || !sameFrag(previous, f) {
			flush()
			previous = f
		}
		current = append(current, []rune(u.text)...)
	}
	flush()
	return &fragLine{extra: extraSpace, wordCount: 1, words: words, fontSize: maxSize, ascent: maxAscent, descent: minDescent,
		maxWidth: maxWidth, lineBreak: lineBreak}
}

// cjkFragSplit is cjkFragSplit: the lines of the fragments of a wordWrap 'CJK'
// paragraph, broken between characters.
func (p *paragraph) cjkFragSplit(maxWidths []float64, calcBounds bool) {
	var U []cjkUnit
	for _, f := range p.frags {
		if f.text != "" {
			for _, r := range f.text {
				U = append(U, cjkUnitOf(string(r), f))
			}
			continue
		}
		U = append(U, cjkUnitOf("", f))
	}
	var lines []*fragLine
	i, lineStartPos := 0, 0
	widthUsed := 0.0
	maxWidth := maxWidths[0]
	nU := len(U)
	for i < nU {
		u := U[i]
		i++
		w := u.cjkWidth(maxWidth)
		widthUsed += w
		lineBreak := u.f.lineBreak
		endLine := (widthUsed > maxWidth+fuzz && widthUsed > 0) || lineBreak
		if !endLine {
			continue
		}
		extraSpace := maxWidth - widthUsed
		if !lineBreak {
			if cjkOrd(u) < 0x3000 {
				// A section of a non-Asian script: look back for a space or a
				// return to Kanji, or half way.
				limitCheck := (lineStartPos + i) >> 1
				for j := i - 1; j > limitCheck; j-- {
					uj := U[j]
					rj := cjkOrd(uj)
					if unicode.Is(unicode.Zs, rj) || rj >= 0x3000 {
						k := j + 1
						if k < i {
							j = k + 1
							for ii := j; ii < i; ii++ {
								extraSpace += U[ii].width
							}
							w = U[k].width
							u = U[k]
							i = j
							break
						}
					}
				}
			}
			if !strings.ContainsRune(allCannotStart, cjkOrd(u)) && i > lineStartPos+1 {
				i--
				extraSpace += w
			}
		}
		lines = append(lines, makeCJKParaLine(U[lineStartPos:i], maxWidth, widthUsed, extraSpace, lineBreak, calcBounds))
		if len(lines) < len(maxWidths) {
			maxWidth = maxWidths[len(lines)]
		} else {
			maxWidth = maxWidths[len(maxWidths)-1]
		}
		lineStartPos = i
		widthUsed = 0
	}
	if widthUsed > 0 {
		lines = append(lines, makeCJKParaLine(U[lineStartPos:], maxWidth, widthUsed, maxWidth-widthUsed, false, calcBounds))
	}
	p.fragLines = lines
}

// firstRune is the character of a cjkU, or 0 for the empty text of a fragment.
func firstRune(text string) rune {
	for _, r := range text {
		return r
	}
	return 0
}

// cjkOrd is ord(u): the character of a cjkU, which an empty text has none of.
func cjkOrd(u cjkUnit) rune {
	if u.text == "" {
		panic(paraFailure{"ord() expected a character, but string of length 0 found"})
	}
	return firstRune(u.text)
}

// breakCJK is Paragraph.breakLinesCJK: the lines of a wordWrap 'CJK'
// paragraph.
func (p *paragraph) breakCJK(maxWidths []float64) {
	p.kind, p.lines, p.fragLines = 0, nil, nil
	if len(p.frags) == 1 && p.frags[0].img == nil && p.frags[0].anchor == nil && p.style.endDots == "" {
		f := p.frags[0]
		text := f.text
		if f.hasWords {
			var joined []rune
			for _, word := range f.words {
				joined = append(joined, word.text...)
			}
			text = string(joined)
		}
		for _, line := range wordSplit(text, maxWidths, f.font, f.size.float()) {
			p.lines = append(p.lines, line)
		}
		p.lineFrag = f
		p.ascent, p.descent = f.size, numMulOrErr(numFloat(-0.2), f.size)
		return
	}
	p.kind = 1
	p.cjkFragSplit(maxWidths, p.style.autoLeading != "" && p.style.autoLeading != "off")
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
	if style.wordWrap == "RTL" {
		indent = numAddOrErr(style.rightIndent, style.firstLineIndent)
	}
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
	p.wrapWidths = [2]float64{widths[0], widths[1]}
	switch {
	case p.count() == 0:
		p.kind, p.lines, p.fragLines, p.lineFrag = 0, nil, nil, &frag{font: fontsByName[style.fontName], size: style.fontSize,
			color: style.textColor}
		p.ascent, p.descent = style.fontSize, numMulOrErr(numFloat(-0.2), style.fontSize)
	case p.keptFragLines != nil:
		// breakLinesCJK answers the lines a split made.
		p.kind, p.fragLines = 1, p.keptFragLines
	case p.keptLines != nil:
		p.kind, p.lines = 0, p.keptLines
		p.lineFrag = p.frags[0]
		p.ascent, p.descent = p.frags[0].size, numMulOrErr(numFloat(-0.2), p.frags[0].size)
	case style.wordWrap == "CJK":
		p.breakCJK(widths)
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

// hardPart is _split_blParaHard: the fragments of some lines, a space after the
// last text fragment of each line but the last.
func (p *paragraph) hardPart(start, stop int) *paragraph {
	var frags []*frag
	lines := p.fragLines[start:stop]
	for i, line := range lines {
		frags = append(frags, line.words...)
		if i == len(lines)-1 {
			continue
		}
		j := len(frags) - 1
		for j >= 0 && ((frags[j].img != nil && !frags[j].img.width.truthy()) || frags[j].anchor != nil) {
			j--
		}
		if j >= 0 {
			g := frags[j]
			if g.text == "" {
				g.text = " "
			} else if !strings.HasSuffix(g.text, " ") {
				g.text += " "
			}
		}
	}
	return &paragraph{style: p.style, frags: frags}
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
		if p.processed != nil {
			first, second = p.processedPart(0, s), p.processedPart(s, n)
		} else {
			first, second = p.hardPart(0, s), p.hardPart(s, n)
		}
	} else {
		first, second = p.simplePart(p.lines[:s]), p.simplePart(p.lines[s:])
	}
	if p.style.wordWrap == "CJK" {
		// breakLinesCJK keeps the lines a split made instead of breaking the
		// words again.
		if p.kind == 1 {
			first.keptFragLines = p.fragLines[:s]
		} else {
			first.keptLines = p.lines[:s]
		}
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
