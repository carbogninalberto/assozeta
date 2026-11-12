// Rendering: Tag.decode(indent_level=0, eventual_encoding="utf-8",
// formatter="minimal") and the Formatter/EntitySubstitution pieces it uses.
package bs4prettify

import (
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
)

// maxOutputBytes bounds the output; past it Prettify returns ErrUnsupported.
const maxOutputBytes = 32 << 20

const (
	startEvent = iota
	endEvent
	emptyEvent
	stringEvent
)

type renderer struct {
	out           strings.Builder
	level         int
	stringLiteral *node
}

func render(root *node) (string, error) {
	r := &renderer{}
	type item struct {
		n    *node
		exit bool
	}
	// BeautifulSoup is hidden, so _self_and omits it: rendering starts with
	// the document's children (at indent_level 0).
	stack := make([]item, 0, len(root.children))
	for i := len(root.children) - 1; i >= 0; i-- {
		stack = append(stack, item{root.children[i], false})
	}
	for len(stack) > 0 {
		it := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		n := it.n
		if it.exit {
			if err := r.emit(endEvent, n); err != nil {
				return "", err
			}
			continue
		}
		if !n.isTag() {
			if err := r.emit(stringEvent, n); err != nil {
				return "", err
			}
			continue
		}
		if n.isEmptyElement() {
			if err := r.emit(emptyEvent, n); err != nil {
				return "", err
			}
			continue
		}
		if err := r.emit(startEvent, n); err != nil {
			return "", err
		}
		stack = append(stack, item{n, true})
		for i := len(n.children) - 1; i >= 0; i-- {
			stack = append(stack, item{n.children[i], false})
		}
	}
	return r.out.String(), nil
}

// emit is the body of Tag.decode's event loop for one event.
func (r *renderer) emit(event int, n *node) error {
	var piece string
	switch event {
	case startEvent, emptyEvent:
		piece = formatTag(n, true)
	case endEvent:
		piece = formatTag(n, false)
		r.level--
	default:
		piece = stringOutput(n)
	}

	indentBefore, indentAfter := true, true
	if r.stringLiteral != nil {
		indentBefore, indentAfter = false, false
	}
	if event == startEvent && r.stringLiteral == nil && !shouldPrettyPrint(n) {
		indentBefore, indentAfter = true, false
		r.stringLiteral = n
	} else if event == endEvent && n == r.stringLiteral {
		indentBefore, indentAfter = false, true
		r.stringLiteral = nil
	}

	if indentBefore || indentAfter {
		if event == stringEvent {
			piece = stripPythonSpace(piece)
		}
		if piece != "" {
			if err := r.writeIndented(piece, indentBefore, indentAfter); err != nil {
				return err
			}
		}
	} else if err := r.write(piece); err != nil {
		return err
	}
	if event == startEvent {
		r.level++
	}
	return nil
}

func shouldPrettyPrint(n *node) bool { return !preserveWhitespaceTags[n.name] }

func (r *renderer) write(piece string) error {
	if r.out.Len()+len(piece) > maxOutputBytes {
		return ErrUnsupported
	}
	r.out.WriteString(piece)
	return nil
}

// writeIndented is _indent_string with formatter.indent == " ". It checks the
// size before writing the indentation.
func (r *renderer) writeIndented(piece string, before, after bool) error {
	spaces := 0
	if before && r.level > 0 {
		spaces = r.level
	}
	newline := 0
	if after {
		newline = 1
	}
	if r.out.Len()+spaces+len(piece)+newline > maxOutputBytes {
		return ErrUnsupported
	}
	for spaces > 0 {
		chunk := spaces
		if chunk > len(indentSpaces) {
			chunk = len(indentSpaces)
		}
		r.out.WriteString(indentSpaces[:chunk])
		spaces -= chunk
	}
	r.out.WriteString(piece)
	if newline > 0 {
		r.out.WriteByte('\n')
	}
	return nil
}

const indentSpaces = "                                                                "

// stripPythonSpace is str.strip().
func stripPythonSpace(text string) string {
	runes := pystr.Runes(text)
	start, end := 0, len(runes)
	for start < end && pystr.IsSpace(runes[start]) {
		start++
	}
	for end > start && pystr.IsSpace(runes[end-1]) {
		end--
	}
	if start == 0 && end == len(runes) {
		return text
	}
	return pystr.String(runes[start:end])
}

// formatTag is Tag._format_tag.
func formatTag(n *node, opening bool) string {
	if n.hidden {
		return ""
	}
	var out strings.Builder
	out.WriteByte('<')
	if !opening {
		out.WriteByte('/')
	}
	out.WriteString(n.name)
	if opening {
		for _, a := range sortAttributes(n.attrs) {
			value := a.value
			if a.isList {
				value = strings.Join(a.list, " ")
			}
			switch {
			case a.charset:
				value = charsetSubstitute(value)
			case a.content:
				value = contentSubstitute(value)
			}
			out.WriteByte(' ')
			out.WriteString(a.name)
			out.WriteByte('=')
			out.WriteString(quotedAttributeValue(substituteXML(value)))
		}
	}
	if n.isEmptyElement() {
		out.WriteByte('/')
	}
	out.WriteByte('>')
	return out.String()
}

// stringOutput is NavigableString.output_ready and controls when substitution
// is skipped.
func stringOutput(n *node) string {
	if prefix, suffix, ok := preformattedAffixes(n.kind); ok {
		return prefix + n.text + suffix
	}
	if n.parent == nil || n.parent.name != "script" && n.parent.name != "style" {
		return substituteXML(n.text)
	}
	return n.text
}

func preformattedAffixes(kind string) (string, string, bool) {
	switch kind {
	case "Comment":
		return "<!--", "-->", true
	case "CData":
		return "<![CDATA[", "]]>", true
	case "ProcessingInstruction":
		return "<?", ">", true
	case "Declaration":
		return "<?", "?>", true
	case "Doctype":
		return "<!DOCTYPE ", ">\n", true
	}
	return "", "", false
}

var xmlEscaper = strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;")

// substituteXML is EntitySubstitution.substitute_xml.
func substituteXML(value string) string { return xmlEscaper.Replace(value) }

// quotedAttributeValue is EntitySubstitution.quoted_attribute_value.
func quotedAttributeValue(value string) string {
	quote := "\""
	if strings.Contains(value, "\"") {
		if strings.Contains(value, "'") {
			value = strings.ReplaceAll(value, "\"", "&quot;")
		} else {
			quote = "'"
		}
	}
	return quote + value + quote
}

// charsetSubstitute is CharsetMetaAttributeValue.substitute_encoding("utf-8").
func charsetSubstitute(original string) string { return "utf-8" }

// contentSubstitute is ContentMetaAttributeValue.substitute_encoding("utf-8")
// with CHARSET_RE = ((^|;)\s*charset=)([^;]*), re.M.
func contentSubstitute(original string) string {
	runes := pystr.Runes(original)
	var out strings.Builder
	pos := 0
	for pos < len(runes) {
		start, valueStart, valueEnd := charsetMatch(runes, pos)
		if start < 0 {
			break
		}
		out.WriteString(pystr.String(runes[pos:start]))
		out.WriteString(pystr.String(runes[start:valueStart]))
		out.WriteString("utf-8")
		pos = valueEnd
	}
	out.WriteString(pystr.String(runes[pos:]))
	return out.String()
}

// charsetMatch finds CHARSET_RE at or after pos. It returns the match start,
// the start of the charset value (the end of group(1)) and the end of the
// value (group(3), up to ';' or the end). start is -1 when there is no match.
func charsetMatch(text []rune, pos int) (start, valueStart, valueEnd int) {
	for i := pos; i < len(text); i++ {
		// group(1) starts with ';', or with nothing at ^ (also under re.M).
		groupStart := -1
		if text[i] == ';' {
			groupStart = i + 1
		} else if i == 0 || text[i-1] == '\n' {
			groupStart = i
		}
		if groupStart < 0 {
			continue
		}
		j := groupStart
		for j < len(text) && pystr.IsSpace(text[j]) {
			j++
		}
		if !hasPrefixAt(text, []rune("charset="), j) {
			continue
		}
		afterPrefix := j + len("charset=")
		end := afterPrefix
		for end < len(text) && text[end] != ';' {
			end++
		}
		return i, afterPrefix, end
	}
	return -1, 0, 0
}
