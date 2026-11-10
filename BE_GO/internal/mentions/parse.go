package mentions

import (
	"strings"
)

func isASCIILetter(c byte) bool { return c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' }

func isTagNameByte(c byte) bool { return isASCIILetter(c) || c >= '0' && c <= '9' || c == '-' }

func isAttrNameByte(c byte) bool {
	return isASCIILetter(c) || c >= '0' && c <= '9' || c == '-' || c == '_' || c == ':' || c == '.'
}

// html.parser's whitespace inside tags.
func isTagSpace(c byte) bool { return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' }

// parse builds the bs4 tree for the supported subset.
func parse(s string) (*node, error) {
	if strings.ContainsAny(s, "\x00\ufeff") {
		return nil, unsupported("NUL or BOM in markup")
	}
	root := &node{name: "[document]"}
	current := root
	var data strings.Builder
	hasData := false
	// bs4's already_closed_empty_element: a void element opened as <br>
	// checks off a later <br/> or </br>, which then leaves its tag open.
	alreadyClosed := map[string]int{}
	// endData: a run of only ASCII spaces becomes "\n" (when it has one) or " ",
	// except inside an open <pre>.
	endData := func() {
		if !hasData {
			return
		}
		text := data.String()
		data.Reset()
		hasData = false
		if !current.preservesWhitespace() && strings.Trim(text, "\x20\x0a\x09\x0c\x0d") == "" {
			if strings.Contains(text, "\n") {
				text = "\n"
			} else {
				text = " "
			}
		}
		current.children = append(current.children, &node{isText: true, text: text, parent: current})
	}
	for i := 0; i < len(s); {
		c := s[i]
		switch {
		case c == '<':
			if i+1 < len(s) && isASCIILetter(s[i+1]) {
				tag, end, selfClosing, err := startTag(s, i)
				if err != nil {
					return nil, err
				}
				if voidElements[tag.name] {
					if selfClosing && alreadyClosed[tag.name] > 0 {
						return nil, unsupported("<%s/> after <%s>", tag.name, tag.name)
					}
					if !selfClosing {
						alreadyClosed[tag.name]++
					}
				}
				endData()
				tag.parent = current
				current.children = append(current.children, tag)
				if !voidElements[tag.name] && !selfClosing {
					current = tag
				}
				i = end
				continue
			}
			if i+2 < len(s) && s[i+1] == '/' && isASCIILetter(s[i+2]) {
				j := i + 2
				for j < len(s) && isTagNameByte(s[j]) {
					j++
				}
				name := strings.ToLower(s[i+2 : j])
				for j < len(s) && isTagSpace(s[j]) {
					j++
				}
				if j >= len(s) || s[j] != '>' {
					return nil, unsupported("end tag syntax")
				}
				if voidElements[name] || current == root || current.name != name {
					return nil, unsupported("end tag </%s> does not close the open element", name)
				}
				endData()
				current = current.parent
				i = j + 1
				continue
			}
			return nil, unsupported("markup at %d", i)
		case c == '&':
			if value, length, ok := reference(s, i); ok {
				data.WriteString(value)
				hasData = true
				i += length
				continue
			}
			// html.parser keeps a lone '&' followed by a character that
			// cannot start a reference.
			if i+1 < len(s) && !isASCIILetter(s[i+1]) && !(s[i+1] >= '0' && s[i+1] <= '9') && s[i+1] != '#' {
				data.WriteByte('&')
				hasData = true
				i++
				continue
			}
			return nil, unsupported("character reference at %d", i)
		default:
			j := i
			for j < len(s) && s[j] != '<' && s[j] != '&' {
				j++
			}
			data.WriteString(s[i:j])
			hasData = true
			i = j
		}
	}
	endData()
	return root, nil
}

// startTag reads "<name attr=... >" or "<name ... />" at s[i].
func startTag(s string, i int) (*node, int, bool, error) {
	j := i + 1
	for j < len(s) && isTagNameByte(s[j]) {
		j++
	}
	name := strings.ToLower(s[i+1 : j])
	if refusedElements[name] {
		return nil, 0, false, unsupported("element <%s>", name)
	}
	tag := &node{name: name}
	seen := map[string]bool{}
	for {
		spaced := false
		for j < len(s) && isTagSpace(s[j]) {
			j++
			spaced = true
		}
		if j >= len(s) {
			return nil, 0, false, unsupported("unterminated tag")
		}
		if s[j] == '>' {
			return tag, j + 1, false, nil
		}
		if s[j] == '/' {
			if j+1 < len(s) && s[j+1] == '>' {
				return tag, j + 2, true, nil
			}
			return nil, 0, false, unsupported("tag syntax")
		}
		if !spaced || !isASCIILetter(s[j]) {
			return nil, 0, false, unsupported("attribute syntax")
		}
		k := j
		for k < len(s) && isAttrNameByte(s[k]) {
			k++
		}
		attrName := strings.ToLower(s[j:k])
		if seen[attrName] {
			return nil, 0, false, unsupported("duplicate attribute %q", attrName)
		}
		seen[attrName] = true
		value := ""
		if k < len(s) && s[k] == '=' {
			k++
			if k >= len(s) || (s[k] != '"' && s[k] != '\'') {
				return nil, 0, false, unsupported("unquoted attribute value")
			}
			quote := s[k]
			end := strings.IndexByte(s[k+1:], quote)
			if end < 0 {
				return nil, 0, false, unsupported("unterminated attribute value")
			}
			raw := s[k+1 : k+1+end]
			if strings.ContainsAny(raw, "<>") {
				return nil, 0, false, unsupported("angle bracket in attribute value")
			}
			decoded, err := unescapeAttribute(raw)
			if err != nil {
				return nil, 0, false, err
			}
			value = decoded
			k = k + 1 + end + 1
		} else if k < len(s) && !isTagSpace(s[k]) && s[k] != '>' && s[k] != '/' {
			return nil, 0, false, unsupported("attribute syntax")
		}
		tag.attrs = append(tag.attrs, attribute{name: attrName, value: value, list: listAttribute(name, attrName)})
		j = k
	}
}

// unescapeAttribute is html.unescape for the supported references; any
// other '&' followed by a letter, digit or '#' is refused (legacy entities
// without ';' would be decoded).
func unescapeAttribute(raw string) (string, error) {
	if !strings.Contains(raw, "&") {
		return raw, nil
	}
	var out strings.Builder
	for i := 0; i < len(raw); {
		if raw[i] != '&' {
			out.WriteByte(raw[i])
			i++
			continue
		}
		if value, length, ok := reference(raw, i); ok {
			out.WriteString(value)
			i += length
			continue
		}
		if i+1 < len(raw) && (isASCIILetter(raw[i+1]) || raw[i+1] >= '0' && raw[i+1] <= '9' || raw[i+1] == '#') {
			return "", unsupported("reference in attribute value")
		}
		out.WriteByte('&')
		i++
	}
	return out.String(), nil
}
