package mentions

import (
	"sort"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// node is a bs4 Tag (name != "") or NavigableString.
type node struct {
	name     string
	attrs    []attribute // in source order; serialized sorted by name
	children []*node
	parent   *node
	text     string
	isText   bool
}

type attribute struct {
	name  string
	value string
	list  bool // a multi-valued attribute (class, rel, ...): whitespace-split
}

// bs4 HTMLTreeBuilder.empty_element_tags.
var voidElements = map[string]bool{"area": true, "base": true, "br": true, "col": true, "embed": true, "hr": true, "img": true,
	"input": true, "keygen": true, "link": true, "menuitem": true, "meta": true, "param": true, "source": true, "track": true,
	"wbr": true, "basefont": true, "bgsound": true, "command": true, "frame": true, "image": true, "isindex": true,
	"nextid": true, "spacer": true}

// Elements html.parser reads as raw text, and bs4's whitespace-preserving ones.
var refusedElements = map[string]bool{"script": true, "style": true, "textarea": true, "title": true, "xmp": true,
	"iframe": true, "noembed": true, "noframes": true, "noscript": true, "plaintext": true, "pre": true, "template": true}

// bs4 HTMLTreeBuilder.DEFAULT_CDATA_LIST_ATTRIBUTES.
func listAttribute(tag, name string) bool {
	switch name {
	case "class", "accesskey", "dropzone":
		return true
	case "rel", "rev":
		return tag == "a" || tag == "link"
	case "headers":
		return tag == "td" || tag == "th"
	case "accept-charset":
		return tag == "form"
	case "archive":
		return tag == "object"
	case "sizes":
		return tag == "icon"
	case "sandbox":
		return tag == "iframe"
	case "for":
		return tag == "output"
	}
	return false
}

func (n *node) attr(name string) (string, bool) {
	for _, a := range n.attrs {
		if a.name == name {
			return a.value, true
		}
	}
	return "", false
}

// classes is the whitespace-split class list.
func (n *node) classes() []string {
	value, ok := n.attr("class")
	if !ok {
		return nil
	}
	return strings.FieldsFunc(value, dto.PythonIsSpace)
}

func (n *node) hasClass(class string) bool {
	for _, c := range n.classes() {
		if c == class {
			return true
		}
	}
	return false
}

// isMentionSpan is find_all('span', {'class': 'mention', 'data-type':
// 'mention'}): a class list item or the joined list equal to 'mention'.
func isMentionSpan(n *node) bool {
	if n.isText || n.name != "span" {
		return false
	}
	if _, ok := n.attr("class"); !ok {
		return false
	}
	classes := n.classes()
	matched := strings.Join(classes, " ") == "mention"
	for _, c := range classes {
		matched = matched || c == "mention"
	}
	dataType, ok := n.attr("data-type")
	return matched && ok && dataType == "mention"
}

func (n *node) findAll(match func(*node) bool) []*node {
	var found []*node
	var walk func(*node)
	walk = func(current *node) {
		for _, child := range current.children {
			if match(child) {
				found = append(found, child)
			}
			walk(child)
		}
	}
	walk(n)
	return found
}

// findAllText is find_all(string=True) on the current tree.
func (n *node) findAllText() []*node {
	return n.findAll(func(child *node) bool { return child.isText })
}

// replaceWithText is replace_with(str): a new NavigableString in place.
// A span already detached with an ancestor is replaced inside that detached
// subtree, which is never printed.
func (n *node) replaceWithText(text string) {
	parent := n.parent
	replacement := &node{isText: true, text: text, parent: parent}
	for i, child := range parent.children {
		if child == n {
			parent.children[i] = replacement
			break
		}
	}
	n.parent = nil
}

// write is decode(formatter="minimal").
func (n *node) write(out *strings.Builder) {
	if n.isText {
		out.WriteString(escapeText(n.text))
		return
	}
	out.WriteString("<" + n.name)
	attrs := append([]attribute(nil), n.attrs...)
	sort.SliceStable(attrs, func(i, j int) bool { return attrs[i].name < attrs[j].name })
	for _, a := range attrs {
		value := a.value
		if a.list {
			value = strings.Join(strings.FieldsFunc(value, dto.PythonIsSpace), " ")
		}
		out.WriteString(" " + a.name + "=" + quoteAttribute(escapeText(value)))
	}
	if voidElements[n.name] && len(n.children) == 0 {
		out.WriteString("/>")
		return
	}
	out.WriteString(">")
	for _, child := range n.children {
		child.write(out)
	}
	out.WriteString("</" + n.name + ">")
}

var minimalEscaper = strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;")

func escapeText(text string) string { return minimalEscaper.Replace(text) }

// quoteAttribute is EntitySubstitution.quoted_attribute_value.
func quoteAttribute(value string) string {
	if strings.Contains(value, `"`) {
		if strings.Contains(value, "'") {
			return `"` + strings.ReplaceAll(value, `"`, "&quot;") + `"`
		}
		return "'" + value + "'"
	}
	return `"` + value + `"`
}

// Named references bs4 and html.unescape both resolve to one character.
var namedReferences = map[string]string{"amp": "&", "lt": "<", "gt": ">", "quot": `"`, "apos": "'", "nbsp": " "}

// reference reads "&name;" or "&#n;"/"&#xh;" at s[i] (s[i] == '&'): the
// character and the length consumed. ok is false for anything else.
func reference(s string, i int) (string, int, bool) {
	end := strings.IndexByte(s[i:], ';')
	if end < 0 || end > 12 {
		return "", 0, false
	}
	body := s[i+1 : i+end]
	if strings.HasPrefix(body, "#") {
		digits, base := body[1:], 10
		if strings.HasPrefix(digits, "x") || strings.HasPrefix(digits, "X") {
			digits, base = digits[1:], 16
		}
		if digits == "" {
			return "", 0, false
		}
		for _, c := range digits {
			if !(c >= '0' && c <= '9' || base == 16 && (c >= 'a' && c <= 'f' || c >= 'A' && c <= 'F')) {
				return "", 0, false
			}
		}
		code, err := strconv.ParseInt(digits, base, 32)
		// Below 256 bs4 decodes windows-1252 (and html.unescape maps C1
		// controls to it): only the ranges where both equal chr() pass.
		if err != nil || code < 0x20 && code != 9 && code != 10 && code != 13 || code >= 0x7f && code < 0xa0 ||
			code >= 0xd800 && code < 0xe000 || code > 0x10ffff || code >= 0xfdd0 && code <= 0xfdef || code&0xfffe == 0xfffe {
			return "", 0, false
		}
		return string(rune(code)), end + 1, true
	}
	value, ok := namedReferences[body]
	if !ok {
		return "", 0, false
	}
	return value, end + 1, true
}
