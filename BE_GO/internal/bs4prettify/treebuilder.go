// Tree construction: BeautifulSoupHTMLParser and BeautifulSoup's pushTag /
// popTag / endData / _popToTag, ported from bs4 4.13.3.
package bs4prettify

import (
	"sort"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pyhtml"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
)

const rootTagName = "[document]"

// voidTags is HTMLTreeBuilder.DEFAULT_EMPTY_ELEMENT_TAGS.
var voidTags = set("area base basefont bgsound br col command embed frame hr image img input isindex keygen link menuitem meta nextid param source spacer track wbr")

// cdataListAttributes is HTMLTreeBuilder.DEFAULT_CDATA_LIST_ATTRIBUTES.
var cdataListAttributes = map[string]map[string]bool{
	"*": set("class accesskey dropzone"), "a": set("rel rev"), "link": set("rel rev"), "td": set("headers"),
	"th": set("headers"), "form": set("accept-charset"), "object": set("archive"), "area": set("rel"),
	"icon": set("sizes"), "iframe": set("sandbox"), "output": set("for"),
}

// preserveWhitespaceTags is HTMLTreeBuilder.DEFAULT_PRESERVE_WHITESPACE_TAGS.
var preserveWhitespaceTags = set("pre textarea")

// stringContainers is HTMLTreeBuilder.DEFAULT_STRING_CONTAINERS: the class name
// each tag gives its text nodes.
var stringContainers = map[string]string{
	"rt": "RubyTextString", "rp": "RubyParenthesisString", "style": "Stylesheet",
	"script": "Script", "template": "TemplateString",
}

// asciiSpaces is BeautifulSoup.ASCII_SPACES.
const asciiSpaces = "\x20\x0a\x09\x0c\x0d"

// attribute is one entry of Tag.attrs. list is set for a cdata-list value;
// charset and content mark the two CharsetMetaAttributeValue stand-ins.
type attribute struct {
	name    string
	value   string
	list    []string
	isList  bool
	charset bool
	content bool
}

// node is a Tag (name != "") or a NavigableString (name == "").
type node struct {
	name     string
	kind     string // string class name
	text     string
	attrs    []attribute
	children []*node
	parent   *node
	hidden   bool
	canEmpty bool
}

func (n *node) isTag() bool { return n.name != "" }

// is_empty_element: len(contents) == 0 and can_be_empty_element is True.
func (n *node) isEmptyElement() bool { return n.isTag() && len(n.children) == 0 && n.canEmpty }

type treeBuilder struct {
	root, current *node
	tagStack      []*node
	openCount     map[string]int
	preserve      []*node
	stringStack   []*node
	data          []string
	hasData       bool
	closedVoids   []string
}

func newTreeBuilder() *treeBuilder {
	root := &node{name: rootTagName, hidden: true}
	b := &treeBuilder{root: root, current: root, openCount: map[string]int{}}
	b.tagStack = append(b.tagStack, root)
	return b
}

// buildTree runs one feed(markup) + close() through the html.parser port and
// the BeautifulSoup tree builder, then _feed's closing endData and pops.
func buildTree(markup string) (*node, error) {
	runes := pystr.Runes(markup)
	if err := preCheck(runes); err != nil {
		return nil, err
	}
	b := newTreeBuilder()
	if err := tokenize(runes, b.handleEvent); err != nil {
		if err == errParserAssertion || err == ErrPythonRaises {
			return nil, ErrPythonRaises
		}
		return nil, err
	}
	b.endData("")
	for len(b.tagStack) > 1 {
		b.popTag()
	}
	return b.root, nil
}

// preCheck is BeautifulSoup.__init__'s warning path for a short str with no
// "<" and no newline. It only matters when _markup_resembles_filename encodes
// the markup, which raises UnicodeEncodeError for a lone surrogate.
func preCheck(markup []rune) error {
	if len(markup) > 256 {
		return nil
	}
	for _, r := range markup {
		if r == '<' || r == '\n' {
			return nil
		}
	}
	url := false
	if hasPrefixAt(markup, []rune("http:"), 0) || hasPrefixAt(markup, []rune("https:"), 0) {
		url = true
		for _, r := range markup {
			if r == ' ' {
				url = false
				break
			}
		}
	}
	if url {
		return nil
	}
	for _, r := range markup {
		if r >= 0xd800 && r <= 0xdfff {
			return ErrPythonRaises
		}
	}
	return nil
}

func (b *treeBuilder) handleEvent(event parserEvent) error {
	switch event.kind {
	case "start":
		b.handleStartTag(event.text, event.attrs, true)
	case "startend":
		b.handleStartTag(event.text, event.attrs, false)
		b.handleEndTag(event.text, true)
	case "end":
		b.handleEndTag(event.text, true)
	case "data":
		b.appendData(event.text)
	case "charref":
		return b.handleCharref(event.text)
	case "entityref":
		b.handleEntityref(event.text)
	case "comment":
		b.endData("")
		b.appendData(event.text)
		b.endData("Comment")
	case "decl":
		b.endData("")
		b.appendData(pystr.Runes(declData(pystr.String(event.text))))
		b.endData("Doctype")
	case "unknown_decl":
		b.handleUnknownDecl(event.text)
	case "pi":
		b.endData("")
		b.appendData(event.text)
		b.endData("ProcessingInstruction")
	}
	return nil
}

// declData is handle_decl's data[len("DOCTYPE "):].
func declData(data string) string {
	runes := pystr.Runes(data)
	if len(runes) > len("DOCTYPE ") {
		return pystr.String(runes[len("DOCTYPE "):])
	}
	return ""
}

func (b *treeBuilder) handleUnknownDecl(data []rune) {
	kind := "Declaration"
	if hasASCIIUpperPrefix(data, "CDATA[") {
		kind = "CData"
		if len(data) >= len("CDATA[") {
			data = data[len("CDATA["):]
		} else {
			data = nil
		}
	}
	b.endData("")
	b.appendData(data)
	b.endData(kind)
}

// hasASCIIUpperPrefix is data.upper().startswith(prefix) for an ASCII prefix.
func hasASCIIUpperPrefix(data []rune, prefix string) bool {
	want := []rune(prefix)
	if len(data) < len(want) {
		return false
	}
	for i, r := range want {
		c := data[i]
		if c >= 'a' && c <= 'z' {
			c -= 'a' - 'A'
		}
		if c != r {
			return false
		}
	}
	return true
}

// handleStartTag is BeautifulSoupHTMLParser.handle_starttag and
// BeautifulSoup.handle_starttag, including Tag.__init__'s attribute handling.
func (b *treeBuilder) handleStartTag(name []rune, raw []rawAttr, handleEmpty bool) {
	b.endData("")
	tagName := pystr.String(name)
	attrs := buildAttributes(tagName, raw)
	applySubstitutions(tagName, attrs)
	tag := &node{
		name:     tagName,
		attrs:    attrs,
		parent:   b.current,
		canEmpty: voidTags[tagName],
	}
	b.pushTag(tag)
	if tag.canEmpty && handleEmpty {
		b.handleEndTag(name, false)
		b.closedVoids = append(b.closedVoids, tagName)
	}
}

// buildAttributes is the duplicate-attribute loop (REPLACE, later value wins,
// first position kept), then _replace_cdata_list_attribute_values.
func buildAttributes(tagName string, raw []rawAttr) []attribute {
	attrs := make([]attribute, 0, len(raw))
	index := map[string]int{}
	for _, item := range raw {
		key := pystr.String(item.name)
		value := ""
		if item.hasValue {
			value = pystr.String(item.value)
		}
		if at, ok := index[key]; ok {
			attrs[at].value = value
			attrs[at].isList = false
			attrs[at].list = nil
			continue
		}
		index[key] = len(attrs)
		attrs = append(attrs, attribute{name: key, value: value})
	}
	universal := cdataListAttributes["*"]
	tagSpecific := cdataListAttributes[tagName]
	for i := range attrs {
		if universal[attrs[i].name] || tagSpecific[attrs[i].name] {
			attrs[i].list = splitNonWhitespace(attrs[i].value)
			attrs[i].isList = true
		}
	}
	return attrs
}

// splitNonWhitespace is nonwhitespace_re.findall(value).
func splitNonWhitespace(value string) []string {
	var result []string
	var current []rune
	for _, r := range pystr.Runes(value) {
		if pystr.IsSpace(r) {
			if len(current) > 0 {
				result = append(result, pystr.String(current))
				current = current[:0]
			}
			continue
		}
		current = append(current, r)
	}
	if len(current) > 0 {
		result = append(result, pystr.String(current))
	}
	return result
}

// applySubstitutions is HTMLTreeBuilder.set_up_substitutions.
func applySubstitutions(tagName string, attrs []attribute) {
	if tagName != "meta" {
		return
	}
	contentAt, charsetAt, httpAt := -1, -1, -1
	for i := range attrs {
		switch attrs[i].name {
		case "content":
			contentAt = i
		case "charset":
			charsetAt = i
		case "http-equiv":
			httpAt = i
		}
	}
	contentType := false
	if httpAt >= 0 {
		if attrs[httpAt].isList {
			for _, value := range attrs[httpAt].list {
				if pystr.Lower(value) == "content-type" {
					contentType = true
					break
				}
			}
		} else if pystr.Lower(attrs[httpAt].value) == "content-type" {
			contentType = true
		}
	}
	if charsetAt >= 0 {
		attrs[charsetAt].charset = true
	} else if contentAt >= 0 && contentType {
		attrs[contentAt].content = true
	}
}

// handleEndTag is BeautifulSoupHTMLParser.handle_endtag / BeautifulSoup.handle_endtag.
func (b *treeBuilder) handleEndTag(name []rune, checkAlreadyClosed bool) {
	text := pystr.String(name)
	if checkAlreadyClosed {
		for i, closed := range b.closedVoids {
			if closed == text {
				b.closedVoids = append(b.closedVoids[:i], b.closedVoids[i+1:]...)
				return
			}
		}
	}
	b.endData("")
	b.popToTag(text)
}

func (b *treeBuilder) appendData(data []rune) {
	b.data = append(b.data, pystr.String(data))
	b.hasData = true
}

// endData is BeautifulSoup.endData. containerClass is "" for NavigableString.
func (b *treeBuilder) endData(containerClass string) {
	if !b.hasData {
		return
	}
	text := strings.Join(b.data, "")
	if len(b.preserve) == 0 {
		strippable := true
		for i := 0; i < len(text); i++ {
			if !strings.ContainsRune(asciiSpaces, rune(text[i])) {
				strippable = false
				break
			}
		}
		if strippable {
			if strings.Contains(text, "\n") {
				text = "\n"
			} else {
				text = " "
			}
		}
	}
	b.data = b.data[:0]
	b.hasData = false

	kind := containerClass
	if kind == "" {
		kind = "NavigableString"
		if len(b.stringStack) > 0 {
			if container, ok := stringContainers[b.stringStack[len(b.stringStack)-1].name]; ok {
				kind = container
			}
		}
	}
	o := &node{kind: kind, text: text, parent: b.current}
	b.current.children = append(b.current.children, o)
}

func (b *treeBuilder) pushTag(tag *node) {
	if b.current != nil {
		b.current.children = append(b.current.children, tag)
	}
	b.tagStack = append(b.tagStack, tag)
	b.current = tag
	if tag.name != rootTagName {
		b.openCount[tag.name]++
	}
	if preserveWhitespaceTags[tag.name] {
		b.preserve = append(b.preserve, tag)
	}
	if _, ok := stringContainers[tag.name]; ok {
		b.stringStack = append(b.stringStack, tag)
	}
}

func (b *treeBuilder) popTag() *node {
	if len(b.tagStack) == 0 {
		return nil
	}
	tag := b.tagStack[len(b.tagStack)-1]
	b.tagStack = b.tagStack[:len(b.tagStack)-1]
	if b.openCount[tag.name] > 0 {
		b.openCount[tag.name]--
	}
	if len(b.preserve) > 0 && b.preserve[len(b.preserve)-1] == tag {
		b.preserve = b.preserve[:len(b.preserve)-1]
	}
	if len(b.stringStack) > 0 && b.stringStack[len(b.stringStack)-1] == tag {
		b.stringStack = b.stringStack[:len(b.stringStack)-1]
	}
	if len(b.tagStack) > 0 {
		b.current = b.tagStack[len(b.tagStack)-1]
	}
	return b.current
}

// popToTag is BeautifulSoup._popToTag.
func (b *treeBuilder) popToTag(name string) {
	if name == rootTagName {
		return
	}
	for len(b.tagStack) > 1 && b.openCount[name] > 0 {
		tag := b.tagStack[len(b.tagStack)-1]
		b.popTag()
		if tag.name == name {
			return
		}
	}
}

// handleCharref is BeautifulSoupHTMLParser.handle_charref.
func (b *treeBuilder) handleCharref(name []rune) error {
	var value uint64
	overflow := false
	if len(name) > 0 && (name[0] == 'x' || name[0] == 'X') {
		strip := name[0]
		start := 0
		for start < len(name) && name[start] == strip {
			start++
		}
		digits := name[start:]
		if len(digits) == 0 {
			return ErrPythonRaises
		}
		zero := 0
		for zero < len(digits) && digits[zero] == '0' {
			zero++
		}
		significant := digits[zero:]
		for _, digit := range significant {
			if digit >= '0' && digit <= '9' || digit >= 'a' && digit <= 'f' || digit >= 'A' && digit <= 'F' {
				continue
			}
			return ErrPythonRaises
		}
		if len(significant) > 6 {
			overflow = true
		} else {
			for _, digit := range significant {
				value = value*16 + uint64(hexValue(digit))
			}
			if value > 0x10ffff {
				overflow = true
			}
		}
	} else {
		// int(name) applies CPython's decimal digit limit.
		if len(name) > 4300 {
			return ErrPythonRaises
		}
		for _, digit := range name {
			if digit < '0' || digit > '9' {
				return ErrPythonRaises
			}
		}
		if len(name) == 0 {
			return ErrPythonRaises
		}
		for _, digit := range name {
			value = value*10 + uint64(digit-'0')
			if value > 0x10ffff {
				overflow = true
				break
			}
		}
	}

	var data []rune
	switch {
	case overflow:
		data = []rune{0xfffd}
	case value < 256:
		if c, ok := windows1252(byte(value)); ok {
			data = []rune{c}
		} else {
			data = []rune{rune(value)}
		}
	default:
		data = []rune{rune(value)}
	}
	b.appendData(data)
	return nil
}

func hexValue(r rune) int {
	switch {
	case r >= '0' && r <= '9':
		return int(r - '0')
	case r >= 'a' && r <= 'f':
		return int(r-'a') + 10
	case r >= 'A' && r <= 'F':
		return int(r-'A') + 10
	}
	return 0
}

// windows1252 is bytearray([n]).decode("windows-1252") for n < 256. The five
// undefined bytes fall through to chr(n).
func windows1252(b byte) (rune, bool) {
	switch b {
	case 0x80:
		return 0x20ac, true
	case 0x81:
		return 0, false
	case 0x82:
		return 0x201a, true
	case 0x83:
		return 0x0192, true
	case 0x84:
		return 0x201e, true
	case 0x85:
		return 0x2026, true
	case 0x86:
		return 0x2020, true
	case 0x87:
		return 0x2021, true
	case 0x88:
		return 0x02c6, true
	case 0x89:
		return 0x2030, true
	case 0x8a:
		return 0x0160, true
	case 0x8b:
		return 0x2039, true
	case 0x8c:
		return 0x0152, true
	case 0x8d:
		return 0, false
	case 0x8e:
		return 0x017d, true
	case 0x8f:
		return 0, false
	case 0x90:
		return 0, false
	case 0x91:
		return 0x2018, true
	case 0x92:
		return 0x2019, true
	case 0x93:
		return 0x201c, true
	case 0x94:
		return 0x201d, true
	case 0x95:
		return 0x2022, true
	case 0x96:
		return 0x2013, true
	case 0x97:
		return 0x2014, true
	case 0x98:
		return 0x02dc, true
	case 0x99:
		return 0x2122, true
	case 0x9a:
		return 0x0161, true
	case 0x9b:
		return 0x203a, true
	case 0x9c:
		return 0x0153, true
	case 0x9d:
		return 0, false
	case 0x9e:
		return 0x017e, true
	case 0x9f:
		return 0x0178, true
	}
	return rune(b), true
}

// handleEntityref is BeautifulSoupHTMLParser.handle_entityref.
func (b *treeBuilder) handleEntityref(name []rune) {
	text := pystr.String(name)
	if character, ok := pyhtml.Entity(text + ";"); ok {
		b.appendData(pystr.Runes(character))
		return
	}
	b.appendData(append([]rune{'&'}, name...))
}

// sortAttributes is Formatter.attributes: sorted by name (Python string order).
func sortAttributes(attrs []attribute) []attribute {
	sorted := make([]attribute, len(attrs))
	copy(sorted, attrs)
	sort.SliceStable(sorted, func(i, j int) bool { return sorted[i].name < sorted[j].name })
	return sorted
}
