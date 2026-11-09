// Package bs4prettify reproduces BeautifulSoup(markup, features="html.parser").prettify()
// of beautifulsoup4 4.13.3 on CPython 3.13.16 for a declared subset of HTML.
//
// The subset is the markup a rich-text editor produces and prettify() itself
// emits: text, allow-listed start/end tags, attributes with quoted or no values,
// and named (allow-listed, ';'-terminated) or numeric character references.
// Everything else (comments, doctypes, processing instructions, script/style
// and the other raw-text elements, unquoted attribute values, stray '<' or '&',
// control characters, lone surrogates) returns ErrUnsupported. Callers refuse
// such input instead of guessing html.parser's error recovery.
//
// Parity is proven by TESTS/adapters/django/bs4_prettify_corpus.py, which runs
// the real bs4 on editor documents and generated inputs.
package bs4prettify

import (
	"errors"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

var ErrUnsupported = errors.New("markup outside the proven prettify subset")

// voidTags is HTMLTreeBuilder.empty_element_tags.
var voidTags = set("area base basefont bgsound br col command embed frame hr image img input isindex keygen link menuitem meta nextid param source spacer track wbr")

// allowedTags are the element names this port accepts. They exclude html.parser's
// raw-text elements and bs4's string containers (script, style, template, rt, rp,
// textarea, title, ...), whose contents follow different rules.
var allowedTags = set("a abbr address article aside b bdi bdo big blockquote br caption center cite code col colgroup dd del details dfn div dl dt em figcaption figure font footer h1 h2 h3 h4 h5 h6 header hr i img input ins kbd label li main mark nav ol p pre q s samp section small span strike strong sub summary sup table tbody td tfoot th thead tr tt u ul var wbr")

// cdataListAttributes is HTMLTreeBuilder.DEFAULT_CDATA_LIST_ATTRIBUTES: values
// split on whitespace and re-joined with single spaces.
var cdataListAttributes = map[string]map[string]bool{
	"*": set("class accesskey dropzone"), "a": set("rel rev"), "link": set("rel rev"), "td": set("headers"),
	"th": set("headers"), "form": set("accept-charset"), "object": set("archive"), "area": set("rel"),
	"icon": set("sizes"), "iframe": set("sandbox"), "output": set("for"),
}

// namedEntities is the accepted subset of EntitySubstitution.HTML_ENTITY_TO_CHARACTER
// (text) and html5 entities (attribute values); both map these names alike.
var namedEntities = map[string]string{
	"amp": "&", "lt": "<", "gt": ">", "quot": `"`, "apos": "'", "nbsp": " ",
	"agrave": "à", "aacute": "á", "egrave": "è", "eacute": "é", "igrave": "ì", "iacute": "í",
	"ograve": "ò", "oacute": "ó", "ugrave": "ù", "uacute": "ú", "Agrave": "À", "Egrave": "È",
	"Eacute": "É", "Igrave": "Ì", "Ograve": "Ò", "Ugrave": "Ù", "ccedil": "ç", "euro": "€",
	"copy": "©", "reg": "®", "trade": "™", "laquo": "«", "raquo": "»", "ldquo": "“", "rdquo": "”",
	"lsquo": "‘", "rsquo": "’", "ndash": "–", "mdash": "—", "hellip": "…", "deg": "°",
	"middot": "·", "bull": "•", "times": "×", "sect": "§",
}

func set(words string) map[string]bool {
	result := map[string]bool{}
	for _, word := range strings.Fields(words) {
		result[word] = true
	}
	return result
}

type node struct {
	name     string // "" for a string
	text     string
	attrs    []attribute
	children []*node
	parent   *node
}

type attribute struct{ key, value string }

// Prettify returns BeautifulSoup(markup, "html.parser").prettify().
func Prettify(markup string) (string, error) {
	root, err := parse(markup)
	if err != nil {
		return "", err
	}
	return render(root), nil
}

// --- parsing: html.parser events fed to BeautifulSoup's tree builder ---------------------------

type builder struct {
	root, current *node
	data          strings.Builder
	hasData       bool
	preserve      []*node  // preserve_whitespace_tag_stack
	closedVoids   []string // BeautifulSoupHTMLParser.already_closed_empty_element
}

func parse(markup string) (*node, error) {
	if !textAllowed(markup) {
		return nil, ErrUnsupported
	}
	root := &node{name: "[document]"}
	b := &builder{root: root, current: root}
	for i := 0; i < len(markup); {
		switch markup[i] {
		case '<':
			n, err := b.tag(markup[i:])
			if err != nil {
				return nil, err
			}
			i += n
		case '&':
			text, n, ok := reference(markup[i:])
			if !ok {
				return nil, ErrUnsupported
			}
			b.data.WriteString(text)
			b.hasData = true
			i += n
		default:
			j := strings.IndexAny(markup[i:], "<&")
			if j < 0 {
				j = len(markup) - i
			}
			b.data.WriteString(markup[i : i+j])
			b.hasData = true
			i += j
		}
	}
	b.endData()
	return root, nil
}

// textAllowed rejects control characters other than tab, newline, carriage
// return and form feed, DEL and C1 controls, and lone surrogate code units.
func textAllowed(s string) bool {
	for i := 0; i < len(s); {
		r, size := utf8.DecodeRuneInString(s[i:])
		if r == utf8.RuneError && size <= 1 {
			return false
		}
		if r < 0x20 && r != '\t' && r != '\n' && r != '\r' && r != '\f' || r >= 0x7f && r <= 0x9f {
			return false
		}
		i += size
	}
	return true
}

func asciiLetter(c byte) bool { return c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' }
func asciiDigit(c byte) bool  { return c >= '0' && c <= '9' }
func tagSpace(c byte) bool    { return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' }

func tagName(s string) int {
	if s == "" || !asciiLetter(s[0]) {
		return 0
	}
	n := 1
	for n < len(s) && (asciiLetter(s[n]) || asciiDigit(s[n])) {
		n++
	}
	return n
}

func (b *builder) tag(s string) (int, error) {
	if strings.HasPrefix(s, "</") {
		n := tagName(s[2:])
		if n == 0 {
			return 0, ErrUnsupported
		}
		name := strings.ToLower(s[2 : 2+n])
		i := 2 + n
		for i < len(s) && tagSpace(s[i]) {
			i++
		}
		if i >= len(s) || s[i] != '>' || !allowedTags[name] {
			return 0, ErrUnsupported
		}
		b.endData()
		if !b.consumeClosedVoid(name) {
			b.endTag(name)
		}
		return i + 1, nil
	}
	n := tagName(s[1:])
	if n == 0 {
		return 0, ErrUnsupported
	}
	name := strings.ToLower(s[1 : 1+n])
	if !allowedTags[name] {
		return 0, ErrUnsupported
	}
	i := 1 + n
	var attrs []attribute
	for {
		spaced := false
		for i < len(s) && tagSpace(s[i]) {
			i++
			spaced = true
		}
		if i >= len(s) {
			return 0, ErrUnsupported
		}
		if s[i] == '>' {
			b.startTag(name, attrs, false)
			return i + 1, nil
		}
		if s[i] == '/' {
			if i+1 < len(s) && s[i+1] == '>' {
				b.startTag(name, attrs, true)
				return i + 2, nil
			}
			return 0, ErrUnsupported
		}
		if !spaced {
			return 0, ErrUnsupported
		}
		start := i
		if !(asciiLetter(s[i]) || s[i] == '_' || s[i] == ':') {
			return 0, ErrUnsupported
		}
		for i < len(s) && (asciiLetter(s[i]) || asciiDigit(s[i]) || strings.IndexByte("_:.-", s[i]) >= 0) {
			i++
		}
		key := strings.ToLower(s[start:i])
		if i < len(s) && s[i] == '=' {
			i++
			if i >= len(s) || s[i] != '"' && s[i] != '\'' {
				return 0, ErrUnsupported
			}
			quote := s[i]
			end := strings.IndexByte(s[i+1:], quote)
			if end < 0 {
				return 0, ErrUnsupported
			}
			value, ok := unescapeAttribute(s[i+1 : i+1+end])
			if !ok {
				return 0, ErrUnsupported
			}
			attrs = append(attrs, attribute{key, value})
			i += end + 2
			if i < len(s) && !tagSpace(s[i]) && s[i] != '>' && s[i] != '/' {
				return 0, ErrUnsupported
			}
		} else {
			// A valueless attribute: html.parser reports None, bs4 stores "".
			attrs = append(attrs, attribute{key, ""})
		}
	}
}

func unescapeAttribute(s string) (string, bool) {
	var out strings.Builder
	for i := 0; i < len(s); {
		if s[i] != '&' {
			j := strings.IndexByte(s[i:], '&')
			if j < 0 {
				j = len(s) - i
			}
			out.WriteString(s[i : i+j])
			i += j
			continue
		}
		text, n, ok := reference(s[i:])
		if !ok {
			return "", false
		}
		out.WriteString(text)
		i += n
	}
	return out.String(), true
}

// reference decodes an allow-listed "&name;" or a "&#N;"/"&#xH;" reference
// whose code point is a printable scalar value that windows-1252 (text) and
// html.unescape (attributes) both map to itself.
func reference(s string) (string, int, bool) {
	end := strings.IndexByte(s, ';')
	if end < 2 || end > 12 {
		return "", 0, false
	}
	body := s[1:end]
	if body[0] != '#' {
		for k := 0; k < len(body); k++ {
			if !asciiLetter(body[k]) && !asciiDigit(body[k]) {
				return "", 0, false
			}
		}
		text, ok := namedEntities[body]
		return text, end + 1, ok
	}
	digits, base := body[1:], 10
	if digits != "" && (digits[0] == 'x' || digits[0] == 'X') {
		digits, base = digits[1:], 16
	}
	if digits == "" || len(digits) > 7 {
		return "", 0, false
	}
	value, err := strconv.ParseUint(digits, base, 32)
	if err != nil {
		return "", 0, false
	}
	r := rune(value)
	switch {
	case r >= 0x20 && r <= 0x7e, r >= 0xa0 && r <= 0xd7ff, r >= 0xe000 && r <= 0xfdcf, r >= 0xfdf0 && r <= 0xfffd,
		r >= 0x10000 && r <= 0x10ffff && r&0xfffe != 0xfffe:
		return string(r), end + 1, true
	}
	return "", 0, false
}

func (b *builder) endData() {
	if !b.hasData {
		return
	}
	text := b.data.String()
	b.data.Reset()
	b.hasData = false
	if len(b.preserve) == 0 && strings.Trim(text, " \n\t\f\r") == "" {
		if strings.Contains(text, "\n") {
			text = "\n"
		} else {
			text = " "
		}
	}
	b.current.children = append(b.current.children, &node{text: text, parent: b.current})
}

func (b *builder) startTag(name string, attrs []attribute, selfClosing bool) {
	b.endData()
	tag := &node{name: name, attrs: normalizeAttributes(name, attrs), parent: b.current}
	b.current.children = append(b.current.children, tag)
	if !selfClosing && voidTags[name] {
		// handle_starttag closes an empty void element at once and remembers
		// its name in already_closed_empty_element.
		b.closedVoids = append(b.closedVoids, name)
		return
	}
	b.current = tag
	if name == "pre" {
		b.preserve = append(b.preserve, tag)
	}
	if !selfClosing {
		return
	}
	// handle_startendtag: handle_endtag(name) with check_already_closed. A name
	// left by an earlier "<br>"-style void element is consumed instead, and the
	// new element stays open (it may then gain children).
	if !b.consumeClosedVoid(name) {
		b.endTag(name)
	}
}

// consumeClosedVoid is handle_endtag's check_already_closed branch: the first
// remembered occurrence of name is removed and the end tag is ignored.
func (b *builder) consumeClosedVoid(name string) bool {
	for i, closed := range b.closedVoids {
		if closed == name {
			b.closedVoids = append(b.closedVoids[:i], b.closedVoids[i+1:]...)
			return true
		}
	}
	return false
}

func (b *builder) endTag(name string) {
	b.endData()
	// _popToTag: only when the name is open; pops through the most recent one.
	open := false
	for t := b.current; t != b.root; t = t.parent {
		if t.name == name {
			open = true
			break
		}
	}
	if !open {
		return
	}
	for {
		t := b.current
		b.current = t.parent
		if len(b.preserve) > 0 && b.preserve[len(b.preserve)-1] == t {
			b.preserve = b.preserve[:len(b.preserve)-1]
		}
		if t.name == name {
			return
		}
	}
}

// normalizeAttributes applies on_duplicate_attribute=REPLACE (the last value
// wins) and the cdata-list attribute whitespace split.
func normalizeAttributes(tag string, attrs []attribute) []attribute {
	var result []attribute
	index := map[string]int{}
	for _, a := range attrs {
		if at, ok := index[a.key]; ok {
			result[at].value = a.value
			continue
		}
		index[a.key] = len(result)
		result = append(result, a)
	}
	for i := range result {
		if cdataListAttributes["*"][result[i].key] || cdataListAttributes[tag][result[i].key] {
			result[i].value = strings.Join(strings.FieldsFunc(result[i].value, isPythonSpace), " ")
		}
	}
	sort.SliceStable(result, func(i, j int) bool { return result[i].key < result[j].key })
	return result
}

// --- rendering: Tag.decode(indent_level=0, formatter="minimal") --------------------------------

// isPythonSpace is str.isspace() (also re's \s for str patterns).
func isPythonSpace(r rune) bool {
	switch {
	case r >= 0x09 && r <= 0x0d, r >= 0x1c && r <= 0x20, r == 0x85, r == 0xa0, r == 0x1680,
		r >= 0x2000 && r <= 0x200a, r == 0x2028, r == 0x2029, r == 0x202f, r == 0x205f, r == 0x3000:
		return true
	}
	return false
}

var minimal = strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;")

func openingTag(t *node) string {
	var out strings.Builder
	out.WriteString("<" + t.name)
	for _, a := range t.attrs {
		value := minimal.Replace(a.value)
		out.WriteString(" " + a.key + "=")
		switch {
		case strings.Contains(value, `"`) && strings.Contains(value, "'"):
			out.WriteString(`"` + strings.ReplaceAll(value, `"`, "&quot;") + `"`)
		case strings.Contains(value, `"`):
			out.WriteString("'" + value + "'")
		default:
			out.WriteString(`"` + value + `"`)
		}
	}
	if voidTags[t.name] && len(t.children) == 0 {
		out.WriteString("/")
	}
	out.WriteString(">")
	return out.String()
}

func render(root *node) string {
	var out strings.Builder
	level := 0
	var literal *node
	emit := func(piece string, isString bool, before, after bool) {
		if before || after {
			if isString {
				piece = strings.TrimFunc(piece, isPythonSpace)
			}
			if piece != "" {
				if before && level > 0 {
					out.WriteString(strings.Repeat(" ", level))
				}
				out.WriteString(piece)
				if after {
					out.WriteString("\n")
				}
			}
			return
		}
		out.WriteString(piece)
	}
	var walk func(n *node)
	walk = func(n *node) {
		pretty := literal == nil
		if n.name == "" {
			emit(minimal.Replace(n.text), true, pretty, pretty)
			return
		}
		if voidTags[n.name] && len(n.children) == 0 {
			emit(openingTag(n), false, pretty, pretty)
			return
		}
		before, after := pretty, pretty
		if pretty && n.name == "pre" {
			before, after, literal = true, false, n
		}
		emit(openingTag(n), false, before, after)
		level++
		for _, child := range n.children {
			walk(child)
		}
		level--
		before, after = literal == nil, literal == nil
		if literal == n {
			before, after, literal = false, true, nil
		}
		emit("</"+n.name+">", false, before, after)
	}
	for _, child := range root.children {
		walk(child)
	}
	return out.String()
}
