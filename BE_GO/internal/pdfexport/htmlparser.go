package pdfexport

import "strings"

// htmlEvents receives what html.parser.HTMLParser (CPython 3.13, with
// convert_charrefs off) hands a subclass. A false return is an exception of
// the handler: the feed stops.
type htmlEvents interface {
	startTag(tag string, attrs []string) bool
	endTag(tag string) bool
	data(text string) bool
	entityRef(name string) bool
	charRef(name string) bool
}

func htmlSpace(r rune) bool { return r == '\t' || r == '\n' || r == '\r' || r == '\f' || r == ' ' }

func htmlLetter(r rune) bool { return r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' }

func htmlDigit(r rune) bool { return r >= '0' && r <= '9' }

func htmlHex(r rune) bool {
	return htmlDigit(r) || r >= 'a' && r <= 'f' || r >= 'A' && r <= 'F'
}

func htmlQuoteSpaceSlash(r rune) bool { return r == '\'' || r == '"' || r == '/' || htmlSpace(r) }

// htmlLower is str.lower() for a tag name; a name str.lower() lengthens never
// equals a known tag and is kept apart.
func htmlLower(name []rune) string {
	for _, r := range name {
		if r == 'İ' {
			return "\x00" + string(name)
		}
	}
	return strings.ToLower(string(name))
}

type htmlParser struct {
	raw       []rune
	events    htmlEvents
	cdataElem string
	escapable bool
}

func (p *htmlParser) hasPrefix(i int, prefix string) bool {
	for _, r := range prefix {
		if i >= len(p.raw) || p.raw[i] != r {
			return false
		}
		i++
	}
	return true
}

func (p *htmlParser) find(r rune, from int) int {
	for i := from; i < len(p.raw); i++ {
		if p.raw[i] == r {
			return i
		}
	}
	return -1
}

// tagName is [a-zA-Z][^\t\n\r\f />]* at i (the letter is there).
func (p *htmlParser) tagName(i int) int {
	i++
	for i < len(p.raw) && !htmlSpace(p.raw[i]) && p.raw[i] != '/' && p.raw[i] != '>' {
		i++
	}
	return i
}

// attribute is one attribute of attrfind_tolerant and locatetagend at i: its
// name and the optional value, without the trailing run. ok is false when no
// attribute starts here.
func (p *htmlParser) attribute(i int) (end int, name string, ok bool) {
	raw, n := p.raw, len(p.raw)
	if i == 0 || i >= n || !htmlQuoteSpaceSlash(raw[i-1]) || htmlSpace(raw[i]) || raw[i] == '/' || raw[i] == '>' {
		return i, "", false
	}
	start := i
	i++
	for i < n && !htmlSpace(raw[i]) && raw[i] != '/' && raw[i] != '=' && raw[i] != '>' {
		i++
	}
	name = strings.ToLower(string(raw[start:i]))
	q := i
	for q < n && htmlSpace(raw[q]) {
		q++
	}
	if q >= n || raw[q] != '=' {
		return i, name, true
	}
	q++
	r := q
	for r < n && htmlSpace(raw[r]) {
		r++
	}
	if r < n && (raw[r] == '\'' || raw[r] == '"') {
		if closing := p.find(raw[r], r+1); closing >= 0 {
			return closing + 1, name, true
		}
		if r > q {
			return r - 1, name, true // an empty bare value before the last space
		}
		return i, name, true
	}
	for r < n && raw[r] != '>' && !htmlSpace(raw[r]) {
		r++
	}
	return r, name, true
}

// locateTagEnd is locatetagend.match at i.
func (p *htmlParser) locateTagEnd(i int) int {
	raw, n := p.raw, len(p.raw)
	i = p.tagName(i)
	skip := func() {
		for i < n && (htmlSpace(raw[i]) || raw[i] == '/') {
			i++
		}
	}
	skip()
	for {
		end, _, ok := p.attribute(i)
		if !ok {
			break
		}
		i = end
		skip()
	}
	if i < n && raw[i] == '>' {
		i++
	}
	return i
}

// skipTolerant is (?:[\t\n\r\f ]|/(?!>))*.
func (p *htmlParser) skipTolerant(i int) int {
	raw, n := p.raw, len(p.raw)
	for i < n && (htmlSpace(raw[i]) || raw[i] == '/' && !(i+1 < n && raw[i+1] == '>')) {
		i++
	}
	return i
}

func (p *htmlParser) setCDATAMode(elem string, escapable bool) {
	p.cdataElem, p.escapable = elem, escapable
}

// interesting is self.interesting.search(rawdata, i): the next '&' or '<', or
// in a raw-text element its end tag (and '&' in an escapable one).
func (p *htmlParser) interesting(i int) int {
	raw, n := p.raw, len(p.raw)
	if p.cdataElem == "" {
		for ; i < n; i++ {
			if raw[i] == '&' || raw[i] == '<' {
				return i
			}
		}
		return -1
	}
	if p.cdataElem == "plaintext" {
		return n
	}
	elem := []rune(p.cdataElem)
	for ; i < n; i++ {
		if raw[i] == '&' && p.escapable {
			return i
		}
		if raw[i] != '<' || i+1 >= n || raw[i+1] != '/' || i+2+len(elem) >= n {
			continue
		}
		same := true
		for k, r := range elem {
			c := raw[i+2+k]
			if c >= 'A' && c <= 'Z' {
				c += 'a' - 'A'
			}
			if c != r {
				same = false
				break
			}
		}
		if next := raw[i+2+len(elem)]; same && (htmlSpace(next) || next == '/' || next == '>') {
			return i
		}
	}
	return -1
}

func (p *htmlParser) parseStartTag(i int) (int, bool) {
	raw := p.raw
	endpos := p.locateTagEnd(i + 1)
	if raw[endpos-1] != '>' {
		return -1, true
	}
	nameEnd := p.tagName(i + 1)
	tag := htmlLower(raw[i+1 : nameEnd])
	k := p.skipTolerant(nameEnd)
	var attrs []string
	for k < endpos {
		end, name, ok := p.attribute(k)
		if !ok {
			break
		}
		attrs = append(attrs, name)
		k = p.skipTolerant(end)
	}
	if k > endpos {
		k = endpos
	}
	end := strings.Trim(string(raw[k:endpos]), pySpaces)
	if end != ">" && end != "/>" {
		return endpos, p.events.data(string(raw[i:endpos]))
	}
	if strings.HasSuffix(end, "/>") {
		return endpos, p.events.startTag(tag, attrs) && p.events.endTag(tag)
	}
	if !p.events.startTag(tag, attrs) {
		return endpos, false
	}
	switch tag {
	case "script", "style", "xmp", "iframe", "noembed", "noframes", "plaintext":
		p.setCDATAMode(tag, false)
	case "textarea", "title":
		p.setCDATAMode(tag, true)
	}
	return endpos, true
}

func (p *htmlParser) parseEndTag(i int) (int, bool) {
	raw, n := p.raw, len(p.raw)
	if p.find('>', i+2) < 0 {
		return -1, true
	}
	if !(i+2 < n && htmlLetter(raw[i+2])) {
		if i+2 < n && raw[i+2] == '>' {
			return i + 3, true
		}
		return p.find('>', i+2) + 1, true // a bogus comment
	}
	j := p.locateTagEnd(i + 2)
	if raw[j-1] != '>' {
		return -1, true
	}
	tag := htmlLower(raw[i+2 : p.tagName(i+2)])
	if !p.events.endTag(tag) {
		return j, false
	}
	p.cdataElem, p.escapable = "", true
	return j, true
}

// parseDeclaration is parse_html_declaration, parse_comment and parse_pi:
// their handlers do nothing.
func (p *htmlParser) parseMarkup(i int) int {
	raw, n := p.raw, len(p.raw)
	switch {
	case p.hasPrefix(i, "<!--"):
		k := i + 4
		if k < n && raw[k] == '>' {
			return k + 1
		}
		if k+1 < n && raw[k] == '-' && raw[k+1] == '>' {
			return k + 2
		}
		for ; k+2 < n; k++ {
			if raw[k] == '-' && raw[k+1] == '-' {
				if raw[k+2] == '>' {
					return k + 3
				}
				if raw[k+2] == '!' && k+3 < n && raw[k+3] == '>' {
					return k + 4
				}
			}
		}
		return -1
	case p.hasPrefix(i, "<?"):
		return p.find('>', i+2) + 1 - boolInt(p.find('>', i+2) < 0)
	case p.hasPrefix(i, "<![CDATA["):
		for k := i + 9; k+2 < n; k++ {
			if raw[k] == ']' && raw[k+1] == ']' && raw[k+2] == '>' {
				return k + 3
			}
		}
		return -1
	case i+9 <= n && strings.ToLower(string(raw[i:i+9])) == "<!doctype":
		if gt := p.find('>', i+9); gt >= 0 {
			return gt + 1
		}
		return -1
	}
	if gt := p.find('>', i+2); gt >= 0 {
		return gt + 1
	}
	return -1
}

func boolInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

// feed is HTMLParser.feed of the whole text: goahead(0), which leaves an
// incomplete construct at the end unread. It reports false when a handler
// failed.
func (p *htmlParser) feed() bool {
	raw, n := p.raw, len(p.raw)
	p.escapable = true
	i := 0
	for i < n {
		j := p.interesting(i)
		if j < 0 {
			if p.cdataElem != "" {
				break
			}
			j = n
		}
		if i < j && !p.events.data(string(raw[i:j])) {
			return false
		}
		i = j
		if i == n {
			break
		}
		switch {
		case raw[i] == '<':
			k, ok := 0, true
			switch {
			case i+1 < n && htmlLetter(raw[i+1]):
				k, ok = p.parseStartTag(i)
			case p.hasPrefix(i, "</"):
				k, ok = p.parseEndTag(i)
			case p.hasPrefix(i, "<!"), p.hasPrefix(i, "<?"):
				k = p.parseMarkup(i)
			case i+1 < n:
				k, ok = i+1, p.events.data("<")
			default:
				return true
			}
			if !ok {
				return false
			}
			if k < 0 {
				return true
			}
			i = k
		case p.hasPrefix(i, "&#"):
			k := i + 2
			if k < n && (raw[k] == 'x' || raw[k] == 'X') {
				k++
				for k < n && htmlHex(raw[k]) {
					k++
				}
				if k == i+3 {
					k = -1
				}
			} else {
				for k < n && htmlDigit(raw[k]) {
					k++
				}
				// The terminator is no hexadecimal digit: digits followed by a-f do not match.
				if k == i+2 || k < n && htmlHex(raw[k]) {
					k = -1
				}
			}
			if k >= 0 && k < n {
				if !p.events.charRef(string(raw[i+2 : k])) {
					return false
				}
				if raw[k] == ';' {
					k++
				}
				i = k
				continue
			}
			// incomplete_charref
			if i+2 < n && (htmlDigit(raw[i+2]) || (raw[i+2] == 'x' || raw[i+2] == 'X') && i+3 < n && htmlHex(raw[i+3])) {
				return true
			}
			if i+3 >= n {
				return true
			}
			if !p.events.data("&#") {
				return false
			}
			i += 2
		default: // '&'
			if i+1 < n && htmlLetter(raw[i+1]) {
				k := i + 2
				for k < n && (htmlLetter(raw[k]) || htmlDigit(raw[k]) || raw[k] == '-' || raw[k] == '.') {
					k++
				}
				if k == n {
					// Backtrack to a name followed by '-' or '.'.
					for k--; k > i+1 && raw[k] != '-' && raw[k] != '.'; k-- {
					}
					if k <= i+1 {
						return true // incomplete
					}
				}
				if !p.events.entityRef(string(raw[i+1 : k])) {
					return false
				}
				if raw[k] == ';' {
					k++
				}
				i = k
				continue
			}
			if i+1 < n && raw[i+1] == '#' {
				return true
			}
			if i+1 >= n {
				return true
			}
			if !p.events.data("&") {
				return false
			}
			i++
		}
	}
	return true
}
