// Port of CPython 3.13.16 Lib/html/parser.py: HTMLParser configured the way
// beautifulsoup4 configures it (convert_charrefs=False, scripting=False) and
// driven as bs4 drives it, with one feed(markup) followed by close().
//
// The code follows the Python source function by function so the two can be
// read side by side. Positions are indexes into a []rune, one rune per Python
// code point, so slicing and len() behave like Python's str.
package bs4prettify

import (
	"errors"
	"slices"

	"github.com/dlclark/regexp2"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pyhtml"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
)

// errParserAssertion is Python's AssertionError from html.parser.
var errParserAssertion = errors.New("html.parser reached an inconsistent state")

// parserEvent is one handler callback. attrs is set for "start" and "startend".
type parserEvent struct {
	kind  string // "start", "startend", "end", "data", "charref", "entityref", "comment", "decl", "pi", "unknown_decl"
	text  []rune // tag name, or the text argument
	attrs []rawAttr
}

// rawAttr is one (name, value) pair as html.parser reports it. hasValue is
// false when Python passed None (an attribute without "=value").
type rawAttr struct {
	name     []rune
	value    []rune
	hasValue bool
}

// CDATA_CONTENT_ELEMENTS and RCDATA_CONTENT_ELEMENTS.
var (
	cdataContentElements  = set("script style xmp iframe noembed noframes")
	rcdataContentElements = set("textarea title")
)

// Package-level patterns from html.parser (re.VERBOSE layouts rewritten on one
// line). Patterns Python calls with .match() start with \G so regexp2 anchors
// them at the position, like re.match(text, pos).
var (
	incompleteRe         = regexp2.MustCompile(`\G&[a-zA-Z#]`, 0)
	entityrefRe          = regexp2.MustCompile(`\G&([a-zA-Z][-.a-zA-Z0-9]*)[^a-zA-Z0-9]`, 0)
	charrefRe            = regexp2.MustCompile(`\G&#(?:[0-9]+|[xX][0-9a-fA-F]+)[^0-9a-fA-F]`, 0)
	incompleteCharrefRe  = regexp2.MustCompile(`\G&#(?:[0-9]|[xX][0-9a-fA-F])`, 0)
	attrCharrefRe        = regexp2.MustCompile(`&(#[0-9]+|#[xX][0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]*)[;=]?`, 0)
	starttagopenRe       = regexp2.MustCompile(`\G<[a-zA-Z]`, 0)
	endtagopenRe         = regexp2.MustCompile(`\G</[a-zA-Z]`, 0)
	picloseRe            = regexp2.MustCompile(`>`, 0)
	commentcloseRe       = regexp2.MustCompile(`--!?>`, 0)
	commentabruptcloseRe = regexp2.MustCompile(`\G-?>`, 0)
	tagfindTolerantRe    = regexp2.MustCompile(`\G([a-zA-Z][^\t\n\r\f />]*)(?:[\t\n\r\f ]|/(?!>))*`, 0)
	attrfindTolerantRe   = regexp2.MustCompile(`\G((?<=['"\t\n\r\f /])[^\t\n\r\f />][^\t\n\r\f /=>]*)([\t\n\r\f ]*=[\t\n\r\f ]*('[^']*'|"[^"]*"|(?!['"])[^>\t\n\r\f ]*))?(?:[\t\n\r\f ]|/(?!>))*`, 0)
	locatetagendRe       = regexp2.MustCompile(`\G[a-zA-Z][^\t\n\r\f />]*[\t\n\r\f /]*(?:(?<=['"\t\n\r\f /])[^\t\n\r\f />][^\t\n\r\f /=>]*(?:[\t\n\r\f ]*=[\t\n\r\f ]*(?:'[^']*'|"[^"]*"|(?!['"])[^>\t\n\r\f ]*))?[\t\n\r\f /]*)*>?`, 0)
)

// What self.interesting can be. html.parser stores a compiled pattern there;
// the port only needs to know which search to run.
const (
	interestingNormal = iota
	interestingPlaintext
	interestingElement
)

// htmlParser holds the state html.parser keeps between goahead() calls.
type htmlParser struct {
	rawdata      []rune
	lasttag      []rune
	interesting  int
	cdataElem    []rune // self.cdata_elem, nil when None
	supportCdata bool   // self._support_cdata
	escapable    bool   // self._escapable
	scripting    bool   // bs4 leaves this False
	handle       func(parserEvent) error
}

// tokenize is HTMLParser(convert_charrefs=False): feed(markup); close().
// handle is called for each callback in order; a non-nil error stops the parse
// and is returned.
func tokenize(markup []rune, handle func(parserEvent) error) error {
	p := &htmlParser{
		rawdata:      markup,
		lasttag:      []rune("???"),
		interesting:  interestingNormal,
		supportCdata: true,
		escapable:    true,
		handle:       handle,
	}
	if err := p.goahead(false); err != nil {
		return err
	}
	return p.goahead(true)
}

// goahead is HTMLParser.goahead. The first call (end=false) parses as far as
// the text allows, the second (end=true) forces the rest as if followed by EOF.
func (p *htmlParser) goahead(end bool) error {
	rawdata := p.rawdata
	n := len(rawdata)
	i := 0
scan:
	for i < n {
		// convert_charrefs is false, so html.parser goes straight here.
		j := p.searchInteresting(i)
		if j < 0 {
			if p.cdataElem != nil {
				break
			}
			j = n
		}
		if i < j {
			if err := p.handle(parserEvent{kind: "data", text: rawdata[i:j]}); err != nil {
				return err
			}
		}
		i = j
		if i == n {
			break
		}
		if rawdata[i] == '<' {
			var k int
			var err error
			switch {
			case matchesAt(starttagopenRe, rawdata, i): // < + letter
				k, err = p.parseStartTag(i)
			case hasPrefixAt(rawdata, ltSlash, i):
				k, err = p.parseEndTag(i)
			case hasPrefixAt(rawdata, ltBangDashDash, i):
				k, err = p.parseComment(i, true)
			case hasPrefixAt(rawdata, ltQuestion, i):
				k, err = p.parsePI(i)
			case hasPrefixAt(rawdata, ltBang, i):
				k, err = p.parseHTMLDeclaration(i)
			case i+1 < n || end:
				err = p.handle(parserEvent{kind: "data", text: rawdata[i : i+1]})
				k = i + 1
			default:
				break scan
			}
			if err != nil {
				return err
			}
			if k < 0 {
				if !end {
					break scan
				}
				if err := p.recoverAtEOF(rawdata, i, n); err != nil {
					return err
				}
				k = n
			}
			i = k
			continue
		}
		if hasPrefixAt(rawdata, ampHash, i) {
			m, err := findMatchAt(charrefRe, rawdata, i)
			if err != nil {
				return err
			}
			if m != nil {
				whole := rawdata[m.Index : m.Index+m.Length]
				if err := p.handle(parserEvent{kind: "charref", text: whole[2 : len(whole)-1]}); err != nil {
					return err
				}
				k := m.Index + m.Length
				if rawdata[k-1] != ';' {
					k--
				}
				i = k
				continue
			}
			m, err = findMatchAt(incompleteCharrefRe, rawdata, i)
			if err != nil {
				return err
			}
			if m != nil {
				if end {
					if err := p.handle(parserEvent{kind: "charref", text: rawdata[i+2:]}); err != nil {
						return err
					}
					i = n
				}
				break scan // incomplete
			} else if i+3 < n {
				// Not the end of the buffer, and can't be confused with
				// some other construct.
				if err := p.handle(parserEvent{kind: "data", text: rawdata[i : i+2]}); err != nil {
					return err
				}
				i = i + 2
			} else {
				break scan
			}
		} else if hasPrefixAt(rawdata, amp, i) {
			m, err := findMatchAt(entityrefRe, rawdata, i)
			if err != nil {
				return err
			}
			if m != nil {
				if err := p.handle(parserEvent{kind: "entityref", text: groupRunes(m, 1)}); err != nil {
					return err
				}
				k := m.Index + m.Length
				if rawdata[k-1] != ';' {
					k--
				}
				i = k
				continue
			}
			m, err = findMatchAt(incompleteRe, rawdata, i)
			if err != nil {
				return err
			}
			if m != nil {
				if end {
					if err := p.handle(parserEvent{kind: "entityref", text: rawdata[i+1:]}); err != nil {
						return err
					}
					i = n
				}
				break scan // incomplete
			} else if i+1 < n {
				if err := p.handle(parserEvent{kind: "data", text: rawdata[i : i+1]}); err != nil {
					return err
				}
				i = i + 1
			} else {
				break scan
			}
		} else {
			return errParserAssertion // "interesting.search() lied"
		}
	}
	// end of the while loop
	if end && i < n {
		if err := p.handle(parserEvent{kind: "data", text: rawdata[i:n]}); err != nil {
			return err
		}
		i = n
	}
	p.rawdata = rawdata[i:]
	return nil
}

// recoverAtEOF is the `if k < 0: if not end: break` branch of goahead() with
// end set: report what was left of an unterminated construct.
func (p *htmlParser) recoverAtEOF(rawdata []rune, i, n int) error {
	startswith := func(prefix string) bool { return hasPrefixAt(rawdata, []rune(prefix), i) }
	switch {
	case matchesAt(starttagopenRe, rawdata, i): // < + letter
		// unterminated start tag: dropped
	case startswith("</"):
		if i+2 == n {
			return p.handle(parserEvent{kind: "data", text: rawdata[i : i+2]})
		} else if matchesAt(endtagopenRe, rawdata, i) { // </ + letter
			// unterminated end tag: dropped
		} else {
			// bogus comment
			return p.handle(parserEvent{kind: "comment", text: rawdata[i+2:]})
		}
	case startswith("<!--"):
		j := n
		for _, suffix := range []string{"--!", "--", "-"} {
			if endsWithAt(rawdata, []rune(suffix), i+4) {
				j -= len(suffix)
				break
			}
		}
		return p.handle(parserEvent{kind: "comment", text: rawdata[i+4 : j]})
	case startswith("<![CDATA[") && p.supportCdata:
		return p.handle(parserEvent{kind: "unknown_decl", text: rawdata[i+3:]})
	case lowerEquals(prefixRunes(rawdata, i, 9), "<!doctype"):
		return p.handle(parserEvent{kind: "decl", text: rawdata[i+2:]})
	case startswith("<!"):
		// bogus comment
		return p.handle(parserEvent{kind: "comment", text: rawdata[i+2:]})
	case startswith("<?"):
		return p.handle(parserEvent{kind: "pi", text: rawdata[i+2:]})
	default:
		return errParserAssertion // "we should not get here!"
	}
	return nil
}

// searchInteresting is self.interesting.search(text, pos): the leftmost
// position at or after pos where the current pattern matches, or -1.
func (p *htmlParser) searchInteresting(from int) int {
	rawdata := p.rawdata
	switch p.interesting {
	case interestingPlaintext:
		// \Z: matches only at the very end of the text.
		return len(rawdata)
	case interestingElement:
		best := -1
		if p.escapable {
			// escapable and not convert_charrefs: "&|</elem(?=...)".
			for k := from; k < len(rawdata); k++ {
				if rawdata[k] == '&' {
					best = k
					break
				}
			}
		}
		if k := p.findCDATAEnd(from); k >= 0 && (best < 0 || k < best) {
			best = k
		}
		return best
	}
	for k := from; k < len(rawdata); k++ {
		if c := rawdata[k]; c == '&' || c == '<' {
			return k
		}
	}
	return -1
}

// findCDATAEnd finds "</elem" followed by [\t\n\r\f />], with ASCII-only case
// folding (the pattern html.parser builds with re.IGNORECASE|re.ASCII).
func (p *htmlParser) findCDATAEnd(from int) int {
	rawdata := p.rawdata
	elem := p.cdataElem
	for k := from; k+len(elem)+2 < len(rawdata); k++ {
		if rawdata[k] != '<' || rawdata[k+1] != '/' {
			continue
		}
		if !asciiEqualFoldAt(rawdata, elem, k+2) {
			continue
		}
		switch rawdata[k+2+len(elem)] {
		case '\t', '\n', '\r', '\f', ' ', '/', '>':
			return k
		}
	}
	return -1
}

// setCDATAMode is HTMLParser.set_cdata_mode.
func (p *htmlParser) setCDATAMode(elem []rune, escapable bool) {
	p.cdataElem = pystr.LowerRunes(elem)
	p.escapable = escapable
	if equalRunes(p.cdataElem, plaintext) {
		p.interesting = interestingPlaintext
	} else {
		p.interesting = interestingElement
	}
}

// clearCDATAMode is HTMLParser.clear_cdata_mode.
func (p *htmlParser) clearCDATAMode() {
	p.interesting = interestingNormal
	p.cdataElem = nil
	p.escapable = true
}

// parseHTMLDeclaration is HTMLParser.parse_html_declaration.
func (p *htmlParser) parseHTMLDeclaration(i int) (int, error) {
	rawdata := p.rawdata
	if !hasPrefixAt(rawdata, ltBang, i) {
		return 0, errParserAssertion // unexpected call to parse_html_declaration()
	}
	switch {
	case hasPrefixAt(rawdata, ltBangDashDash, i):
		// this case is actually already handled in goahead()
		return p.parseComment(i, true)
	case hasPrefixAt(rawdata, cdataOpen, i) && p.supportCdata:
		j := findRunes(rawdata, cdataClose, i+9)
		if j < 0 {
			return -1, nil
		}
		if err := p.handle(parserEvent{kind: "unknown_decl", text: rawdata[i+3 : j]}); err != nil {
			return 0, err
		}
		return j + 3, nil
	case lowerEquals(prefixRunes(rawdata, i, 9), "<!doctype"):
		// find the closing >
		gtpos := findRunes(rawdata, gt, i+9)
		if gtpos == -1 {
			return -1, nil
		}
		if err := p.handle(parserEvent{kind: "decl", text: rawdata[i+2 : gtpos]}); err != nil {
			return 0, err
		}
		return gtpos + 1, nil
	default:
		return p.parseBogusComment(i, true)
	}
}

// parseComment is HTMLParser.parse_comment.
func (p *htmlParser) parseComment(i int, report bool) (int, error) {
	rawdata := p.rawdata
	if !hasPrefixAt(rawdata, ltBangDashDash, i) {
		return 0, errParserAssertion // unexpected call to parse_comment()
	}
	// An empty comment is abruptly closed by the first ">" or "->", taking
	// priority over a later "-->" or "--!>" close.
	m, err := findMatchAt(commentabruptcloseRe, rawdata, i+4)
	if err != nil {
		return 0, err
	}
	if m == nil {
		m, err = findSearchFrom(commentcloseRe, rawdata, i+4)
		if err != nil {
			return 0, err
		}
		if m == nil {
			return -1, nil
		}
	}
	if report {
		if err := p.handle(parserEvent{kind: "comment", text: rawdata[i+4 : m.Index]}); err != nil {
			return 0, err
		}
	}
	return m.Index + m.Length, nil
}

// parseBogusComment is HTMLParser.parse_bogus_comment.
func (p *htmlParser) parseBogusComment(i int, report bool) (int, error) {
	rawdata := p.rawdata
	if !hasPrefixAt(rawdata, ltBang, i) && !hasPrefixAt(rawdata, ltSlash, i) {
		return 0, errParserAssertion // unexpected call to parse_bogus_comment()
	}
	pos := findRunes(rawdata, gt, i+2)
	if pos == -1 {
		return -1, nil
	}
	if report {
		if err := p.handle(parserEvent{kind: "comment", text: rawdata[i+2 : pos]}); err != nil {
			return 0, err
		}
	}
	return pos + 1, nil
}

// parsePI is HTMLParser.parse_pi.
func (p *htmlParser) parsePI(i int) (int, error) {
	rawdata := p.rawdata
	if !hasPrefixAt(rawdata, ltQuestion, i) {
		return 0, errParserAssertion // unexpected call to parse_pi()
	}
	m, err := findSearchFrom(picloseRe, rawdata, i+2) // >
	if err != nil {
		return 0, err
	}
	if m == nil {
		return -1, nil
	}
	if err := p.handle(parserEvent{kind: "pi", text: rawdata[i+2 : m.Index]}); err != nil {
		return 0, err
	}
	return m.Index + m.Length, nil
}

// parseStartTag is HTMLParser.parse_starttag.
func (p *htmlParser) parseStartTag(i int) (int, error) {
	endpos, err := p.checkForWholeStartTag(i)
	if err != nil {
		return 0, err
	}
	if endpos < 0 {
		return endpos, nil
	}
	rawdata := p.rawdata

	// Now parse the data between i+1 and endpos into a tag and attrs.
	var attrs []rawAttr
	m, err := findMatchAt(tagfindTolerantRe, rawdata, i+1)
	if err != nil {
		return 0, err
	}
	if m == nil {
		return 0, errParserAssertion // unexpected call to parse_starttag()
	}
	k := m.Index + m.Length
	tag := pystr.LowerRunes(groupRunes(m, 1))
	p.lasttag = tag
	for k < endpos {
		m, err := findMatchAt(attrfindTolerantRe, rawdata, k)
		if err != nil {
			return 0, err
		}
		if m == nil {
			break
		}
		attrname := groupRunes(m, 1)
		attrvalue := groupRunes(m, 3)
		if !groupMatched(m, 2) {
			attrs = append(attrs, rawAttr{name: pystr.LowerRunes(attrname)})
		} else {
			attrvalue = stripQuotes(attrvalue)
			if len(attrvalue) > 0 {
				attrvalue = unescapeAttrValue(attrvalue)
			}
			attrs = append(attrs, rawAttr{name: pystr.LowerRunes(attrname), value: attrvalue, hasValue: true})
		}
		k = m.Index + m.Length
	}

	end := stripSpace(subRunes(rawdata, k, endpos))
	if !equalRunes(end, gt) && !equalRunes(end, gtSlash) {
		if err := p.handle(parserEvent{kind: "data", text: subRunes(rawdata, i, endpos)}); err != nil {
			return 0, err
		}
		return endpos, nil
	}
	if endsWithRunes(end, gtSlash) {
		// XHTML-style empty tag: <span attr="value" />
		return endpos, p.handle(parserEvent{kind: "startend", text: tag, attrs: attrs})
	}
	if err := p.handle(parserEvent{kind: "start", text: tag, attrs: attrs}); err != nil {
		return 0, err
	}
	if cdataContentElements[string(tag)] || (p.scripting && equalRunes(tag, noscript)) || equalRunes(tag, plaintext) {
		p.setCDATAMode(tag, false)
	} else if rcdataContentElements[string(tag)] {
		p.setCDATAMode(tag, true)
	}
	return endpos, nil
}

// checkForWholeStartTag is HTMLParser.check_for_whole_start_tag: the end of the
// start tag, or -1 when it is not terminated.
func (p *htmlParser) checkForWholeStartTag(i int) (int, error) {
	m, err := findMatchAt(locatetagendRe, p.rawdata, i+1)
	if err != nil {
		return 0, err
	}
	if m == nil {
		return 0, errParserAssertion
	}
	j := m.Index + m.Length
	if j == 0 || p.rawdata[j-1] != '>' {
		return -1, nil
	}
	return j, nil
}

// parseEndTag is HTMLParser.parse_endtag.
func (p *htmlParser) parseEndTag(i int) (int, error) {
	rawdata := p.rawdata
	if !hasPrefixAt(rawdata, ltSlash, i) {
		return 0, errParserAssertion // unexpected call to parse_endtag
	}
	if findRunes(rawdata, gt, i+2) < 0 { // fast check
		return -1, nil
	}
	if !matchesAt(endtagopenRe, rawdata, i) { // </ + letter
		if hasPrefixAt(rawdata, gt, i+2) { // </> is ignored
			return i + 3, nil
		}
		return p.parseBogusComment(i, true)
	}

	m, err := findMatchAt(locatetagendRe, rawdata, i+2)
	if err != nil {
		return 0, err
	}
	if m == nil {
		return 0, errParserAssertion
	}
	j := m.Index + m.Length
	if rawdata[j-1] != '>' {
		return -1, nil
	}

	// find the name
	m, err = findMatchAt(tagfindTolerantRe, rawdata, i+2)
	if err != nil {
		return 0, err
	}
	if m == nil {
		return 0, errParserAssertion
	}
	tag := pystr.LowerRunes(groupRunes(m, 1))
	if err := p.handle(parserEvent{kind: "end", text: tag}); err != nil {
		return 0, err
	}
	p.clearCDATAMode()
	return j, nil
}

// unescapeAttrValue is _unescape_attrvalue: attr_charref.sub(_replace_attr_charref, s).
func unescapeAttrValue(s []rune) []rune {
	out := make([]rune, 0, len(s))
	pos := 0
	for pos < len(s) {
		m, err := findMatchAt(attrCharrefRe, s, pos)
		if err != nil || m == nil {
			break
		}
		out = append(out, s[pos:m.Index]...)
		out = append(out, replaceAttrCharref(s[m.Index:m.Index+m.Length])...)
		pos = m.Index + m.Length
	}
	return append(out, s[pos:]...)
}

// replaceAttrCharref is _replace_attr_charref.
func replaceAttrCharref(ref []rune) []rune {
	// Numeric / hex char refs must always be unescaped.
	if hasPrefixAt(ref, ampHash, 0) {
		return pystr.Runes(pyhtml.Unescape(pystr.String(ref)))
	}
	// Named character / entity references must only be unescaped if they are
	// an exact match, and they are not followed by an equals sign.
	if ref[len(ref)-1] != '=' {
		if _, ok := pyhtml.Entity(string(ref[1:])); ok {
			return pystr.Runes(pyhtml.Unescape(pystr.String(ref)))
		}
	}
	// Otherwise do not unescape.
	return ref
}

// --- helpers ------------------------------------------------------------------------------------

var (
	amp            = []rune("&")
	ampHash        = []rune("&#")
	ltBang         = []rune("<!")
	ltBangDashDash = []rune("<!--")
	ltQuestion     = []rune("<?")
	ltSlash        = []rune("</")
	gt             = []rune(">")
	gtSlash        = []rune("/>")
	cdataOpen      = []rune("<![CDATA[")
	cdataClose     = []rune("]]>")
	noscript       = []rune("noscript")
	plaintext      = []rune("plaintext")
)

// findMatchAt is pattern.match(text, pos). The patterns used here carry \G, so
// a match must start at pos.
func findMatchAt(re *regexp2.Regexp, text []rune, pos int) (*regexp2.Match, error) {
	if pos < 0 || pos > len(text) {
		return nil, nil
	}
	return re.FindRunesMatchStartingAt(text, pos)
}

// findSearchFrom is pattern.search(text, pos).
func findSearchFrom(re *regexp2.Regexp, text []rune, pos int) (*regexp2.Match, error) {
	if pos < 0 || pos > len(text) {
		return nil, nil
	}
	return re.FindRunesMatchStartingAt(text, pos)
}

func matchesAt(re *regexp2.Regexp, text []rune, pos int) bool {
	m, err := findMatchAt(re, text, pos)
	return err == nil && m != nil
}

func groupMatched(m *regexp2.Match, n int) bool {
	g := m.GroupByNumber(n)
	return g != nil && len(g.Captures) > 0
}

func groupRunes(m *regexp2.Match, n int) []rune {
	if !groupMatched(m, n) {
		return nil
	}
	return m.GroupByNumber(n).Runes()
}

// findRunes is str.find(sub, from).
func findRunes(text, sub []rune, from int) int {
	if from < 0 {
		from = 0
	}
	for i := from; i+len(sub) <= len(text); i++ {
		if hasPrefixAt(text, sub, i) {
			return i
		}
	}
	return -1
}

// hasPrefixAt is str.startswith(prefix, i) for one prefix.
func hasPrefixAt(text, prefix []rune, i int) bool {
	if i < 0 || i+len(prefix) > len(text) {
		return false
	}
	return equalRunes(text[i:i+len(prefix)], prefix)
}

// endsWithAt is str.endswith(suffix, at).
func endsWithAt(text, suffix []rune, at int) bool {
	if at < 0 {
		at = 0
	}
	if at > len(text) {
		return false
	}
	return len(suffix) <= len(text)-at && hasPrefixAt(text, suffix, len(text)-len(suffix))
}

// subRunes is text[start:stop] with Python's slicing rules.
func subRunes(text []rune, start, stop int) []rune {
	n := len(text)
	if start < 0 {
		start += n
		if start < 0 {
			start = 0
		}
	}
	if start > n {
		start = n
	}
	if stop < 0 {
		stop += n
		if stop < 0 {
			stop = 0
		}
	}
	if stop > n {
		stop = n
	}
	if start >= stop {
		return nil
	}
	return text[start:stop]
}

// prefixRunes is text[start:start+length] clamped to the text.
func prefixRunes(text []rune, start, length int) []rune {
	return subRunes(text, start, start+length)
}

func equalRunes(a, b []rune) bool { return slices.Equal(a, b) }

func endsWithRunes(text, suffix []rune) bool {
	return len(suffix) <= len(text) && hasPrefixAt(text, suffix, len(text)-len(suffix))
}

// stripSpace is str.strip(): trims str.isspace() code points.
func stripSpace(text []rune) []rune {
	start, end := 0, len(text)
	for start < end && pystr.IsSpace(text[start]) {
		start++
	}
	for end > start && pystr.IsSpace(text[end-1]) {
		end--
	}
	return text[start:end]
}

// stripQuotes is attrvalue[1:-1] when Python's chained comparison
// attrvalue[:1] == quote == attrvalue[-1:] holds.
func stripQuotes(value []rune) []rune {
	n := len(value)
	if n == 0 || (value[0] != '\'' && value[0] != '"') || value[n-1] != value[0] {
		return value
	}
	if n < 2 {
		return nil // one-character value: ''[1:-1]
	}
	return value[1 : n-1]
}

// lowerEquals is lower(prefix) == target.
func lowerEquals(prefix []rune, target string) bool {
	return equalRunes(pystr.LowerRunes(prefix), []rune(target))
}

// asciiEqualFoldAt is text[i:i+len(lower)] == lower with ASCII-only case
// folding, the re.IGNORECASE|re.ASCII comparison of set_cdata_mode.
func asciiEqualFoldAt(text, lower []rune, i int) bool {
	if i < 0 || i+len(lower) > len(text) {
		return false
	}
	for k, want := range lower {
		got := text[i+k]
		if got >= 'A' && got <= 'Z' {
			got += 'a' - 'A'
		}
		if got != want {
			return false
		}
	}
	return true
}
