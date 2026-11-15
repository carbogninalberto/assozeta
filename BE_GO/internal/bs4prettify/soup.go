// A minimal tree API over the parser in treebuilder.go: the parse tree of
// BeautifulSoup(markup, "html.parser") with the navigation and mutation
// calls another port needs (Tag.get, find_all document order, parent,
// PageElement.replace_with and str(soup)).
//
// Prettify and PrettifyLimited keep their own behaviour; this file only
// exposes the same tree and a non-indented rendering.
package bs4prettify

import "strings"

// Node is one element of the parse tree: a Tag or a NavigableString.
type Node struct {
	n *node
}

// Parse builds the tree BeautifulSoup(markup, "html.parser") builds. It
// returns ErrPythonRaises where bs4 raises on this markup.
func Parse(markup string) (*Node, error) {
	root, err := buildTree(markup)
	if err != nil {
		return nil, err
	}
	return &Node{n: root}, nil
}

// IsTag reports whether the node is a Tag (otherwise a NavigableString).
func (n *Node) IsTag() bool { return n.n.isTag() }

// TagName is Tag.name, and "" for a NavigableString (whose name is None).
func (n *Node) TagName() string { return n.n.name }

// Kind is the NavigableString subclass Tag.name would report to
// find_all(string=True): NavigableString, Comment, CData, Declaration,
// Doctype, ProcessingInstruction, Script, Stylesheet or TemplateString. It
// is "" for a Tag.
func (n *Node) Kind() string { return n.n.kind }

// Text is the string content of a NavigableString and "" for a Tag.
func (n *Node) Text() string { return n.n.text }

// Attr is one Tag attribute after bs4's attribute handling: a cdata-list
// attribute (class, rel, ...) holds a list of str, every other attribute a
// str. Attr is Tag.attrs[name] itself, not Tag.get, so it also reports
// whether the value is a list.
type Attr struct {
	Value  string
	List   []string
	IsList bool
}

// Attr returns Tag.attrs[name] (what Tag.get looks up), and false when the
// tag has no such attribute. On a NavigableString it always reports false.
func (n *Node) Attr(name string) (Attr, bool) {
	if !n.n.isTag() {
		return Attr{}, false
	}
	for _, a := range n.n.attrs {
		if a.name != name {
			continue
		}
		if a.isList {
			return Attr{List: a.list, IsList: true}, true
		}
		return Attr{Value: a.value}, true
	}
	return Attr{}, false
}

// Parent is PageElement.parent: nil for a detached element or the document.
func (n *Node) Parent() *Node {
	if n.n.parent == nil {
		return nil
	}
	return &Node{n: n.n.parent}
}

// Descendants lists Tag.descendants, which is also find_all's document
// order: every descendant pre-order, strings and tags alike, excluding n.
func (n *Node) Descendants() []*Node {
	var found []*Node
	stack := make([]*node, 0, len(n.n.children))
	for i := len(n.n.children) - 1; i >= 0; i-- {
		stack = append(stack, n.n.children[i])
	}
	for len(stack) > 0 {
		current := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		found = append(found, &Node{n: current})
		for i := len(current.children) - 1; i >= 0; i-- {
			stack = append(stack, current.children[i])
		}
	}
	return found
}

// ReplaceWithString is PageElement.replace_with(NavigableString(text)): a
// new plain string at the element's position in its parent. The element is
// detached and keeps its own children and their parent links, like
// PageElement.extract; a detached element's subtree is no longer reached
// from the document but can still be edited.
//
// bs4 raises ValueError here when the element has no parent; that is
// reported as ErrPythonRaises.
func (n *Node) ReplaceWithString(text string) error {
	parent := n.n.parent
	if parent == nil {
		return ErrPythonRaises
	}
	replacement := &node{kind: "NavigableString", text: text, parent: parent}
	replaced := false
	for i, child := range parent.children {
		if child == n.n {
			parent.children[i] = replacement
			replaced = true
			break
		}
	}
	if !replaced {
		// Unreachable: parent links are only set by the builder and by this
		// method, both of which keep the child list in step.
		return ErrPythonRaises
	}
	n.n.parent = nil
	return nil
}

// Decode is str(soup) for a document node: Tag.decode() with
// eventual_encoding="utf-8" and formatter="minimal", i.e. no indentation
// and no trailing newline (a Doctype still adds its own).
func (n *Node) Decode() string {
	var out strings.Builder
	type item struct {
		n    *node
		exit bool
	}
	// decode() walks self_and_descendants, so the walk starts at n itself;
	// the document node is hidden and its _format_tag is "".
	stack := []item{{n: n.n}}
	for len(stack) > 0 {
		it := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		current := it.n
		if it.exit {
			out.WriteString(formatTag(current, false))
			continue
		}
		if !current.isTag() {
			out.WriteString(stringOutput(current))
			continue
		}
		if current.isEmptyElement() {
			out.WriteString(formatTag(current, true))
			continue
		}
		out.WriteString(formatTag(current, true))
		stack = append(stack, item{n: current, exit: true})
		for i := len(current.children) - 1; i >= 0; i-- {
			stack = append(stack, item{n: current.children[i]})
		}
	}
	return out.String()
}
