package smtpsend

// get_mailbox and the parsers it calls from CPython 3.13.16's
// email._header_value_parser, which is the parser django.core.mail.message
// exposes as email.headerregistry.parser: sanitize_address(value, encoding)
// runs get_mailbox on a str address and reads token.display_name,
// token.local_part, token.domain and the returned rest. Only the parts
// reachable from get_mailbox are ported, and the tree keeps just what those
// properties read: a token's class, its text and its children.
//
// The parser works on the bytes of the address: every character it compares
// against a special or whitespace is ASCII, and UTF-8 continuation bytes never
// equal one, so splitting at byte positions matches Python's per-character
// scan.

import (
	"encoding/base64"
	"errors"
	"fmt"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

const (
	// _WSP, CFWS_LEADER, SPECIALS, ATOM_ENDS, DOT_ATOM_ENDS and PHRASE_ENDS.
	pythonWSP   = " \t"
	cfwsLeader  = " \t("
	headerSpecs = "()<>@,:;.\\\"[]"
	atomEnds    = headerSpecs + pythonWSP
	// dotAtomEnds is ATOM_ENDS without '.'.
	dotAtomEnds = "()<>@,:;\\\"[]" + pythonWSP
	// phraseEnds is SPECIALS without '.', '"' and '(' (no whitespace).
	phraseEnds = ")<>@,:;\\[]"
)

// errHeaderIndex is the IndexError some properties raise on an empty token
// list; sanitize_address turns it into a ValueError.
var errHeaderIndex = errors.New("smtpsend: header parse over an empty token")

// headerParseError is email.errors.HeaderParseError.
type headerParseError struct{ msg string }

func (e *headerParseError) Error() string { return "smtpsend: " + e.msg }

func headerParsef(format string, args ...any) error {
	return &headerParseError{msg: fmt.Sprintf(format, args...)}
}

// hdrToken is one node of the parse tree.
type hdrToken struct {
	typ      string
	text     string
	terminal bool
	children []*hdrToken
}

func newTerminal(typ, text string) *hdrToken {
	return &hdrToken{typ: typ, text: text, terminal: true}
}

func newTokenList(typ string, children ...*hdrToken) *hdrToken {
	return &hdrToken{typ: typ, children: children}
}

// pythonDot, pythonListSeparator and pythonRouteMarker are the shared
// ValueTerminals the parser appends.
var (
	pythonDot           = newTerminal("dot", ".")
	pythonListSeparator = newTerminal("list-separator", ",")
	pythonRouteMarker   = newTerminal("route-component-marker", "@")
)

// str is the token's __str__: the canonical form of the text it was parsed
// from, with the minimal quoting.
func (t *hdrToken) str() string {
	switch {
	case t.typ == "ew-fws":
		// EWWhiteSpaceTerminal.__str__ is the empty string.
		return ""
	case t.typ == "bare-quoted-string":
		return quoteString(t.unquoted())
	case t.typ == "comment":
		// Comment.__str__: the parentheses back, with every quoted pair
		// re-escaped (a nested comment already carries its own).
		var out strings.Builder
		out.WriteByte('(')
		for _, child := range t.children {
			if child.typ == "comment" {
				out.WriteString(child.str())
				continue
			}
			out.WriteString(commentQuoted(child.str()))
		}
		out.WriteByte(')')
		return out.String()
	case t.terminal:
		return t.text
	}
	var out strings.Builder
	for _, child := range t.children {
		out.WriteString(child.str())
	}
	return out.String()
}

// unquoted is BareQuotedString.value: the text between the quotes, quoted
// pairs decoded, whitespace preserved.
func (t *hdrToken) unquoted() string {
	var out strings.Builder
	for _, child := range t.children {
		out.WriteString(child.str())
	}
	return out.String()
}

// value is the token's value property.
func (t *hdrToken) value() string {
	switch t.typ {
	case "ew-fws":
		return ""
	case "fws", "cfws", "comment":
		// A whitespace token collapses to one space.
		return " "
	case "bare-quoted-string":
		return t.unquoted()
	case "domain", "domain-literal":
		// ''.join(super().value.split())
		return strings.Join(strings.FieldsFunc(t.listValue(), pySpace), "")
	case "local-part":
		if len(t.children) > 0 && t.children[0].typ == "quoted-string" {
			return t.children[0].quotedValue()
		}
		return t.children[0].value()
	case "addr-spec":
		if len(t.children) < 3 {
			return t.children[0].value()
		}
		return pythonRStrip(t.children[0].value()) + t.children[1].value() + pyLStrip(t.children[2].value())
	}
	if t.terminal {
		return t.text
	}
	return t.listValue()
}

// listValue is TokenList.value: the values of the children that have one.
func (t *hdrToken) listValue() string {
	var out strings.Builder
	for _, child := range t.children {
		out.WriteString(child.value())
	}
	return out.String()
}

// quotedValue is QuotedString.quoted_value.
func (t *hdrToken) quotedValue() string {
	var out strings.Builder
	for _, child := range t.children {
		if child.typ == "bare-quoted-string" {
			out.WriteString(child.str())
			continue
		}
		out.WriteString(child.value())
	}
	return out.String()
}

// commentQuoted is Comment.quote for one child: the characters that would end
// or nest the comment escaped.
func commentQuoted(value string) string {
	return strings.ReplaceAll(strings.ReplaceAll(strings.ReplaceAll(value, `\`, `\\`), "(", `\(`), ")", `\)`)
}

// quoteString is make_quoted_pairs wrapped in double quotes.
func quoteString(value string) string { return `"` + makeQuotedPairs(value) + `"` }

// makeQuotedPairs escapes the dquote and the backslash of a quoted-string.
func makeQuotedPairs(value string) string {
	return strings.ReplaceAll(strings.ReplaceAll(value, `\`, `\\`), `"`, `\"`)
}

// pythonRStrip is str.rstrip() of whitespace.
func pythonRStrip(s string) string { return strings.TrimRightFunc(s, pySpace) }

func ptrTo(value string) *string { return &value }

// displayNameOf is DisplayName.display_name.
func displayNameOf(t *hdrToken) (*string, error) {
	res := append([]*hdrToken{}, t.children...)
	if len(res) == 0 {
		return ptrTo(joinTokenValues(res)), nil
	}
	if res[0].typ == "cfws" {
		res = res[1:]
	} else if !res[0].terminal {
		if len(res[0].children) == 0 {
			return nil, errHeaderIndex
		}
		if res[0].children[0].typ == "cfws" {
			res[0] = newTokenList("list", res[0].children[1:]...)
		}
	}
	if len(res) == 0 {
		return nil, errHeaderIndex
	}
	tail := res[len(res)-1]
	if tail.typ == "cfws" {
		res = res[:len(res)-1]
	} else if !tail.terminal {
		if len(tail.children) == 0 {
			return nil, errHeaderIndex
		}
		if tail.children[len(tail.children)-1].typ == "cfws" {
			res[len(res)-1] = newTokenList("list", tail.children[:len(tail.children)-1]...)
		}
	}
	return ptrTo(joinTokenValues(res)), nil
}

// joinTokenValues is TokenList.value for a bare list of tokens.
func joinTokenValues(tokens []*hdrToken) string {
	var out strings.Builder
	for _, token := range tokens {
		out.WriteString(token.value())
	}
	return out.String()
}

// localPartOf is LocalPart.local_part: the local part with whitespace
// stripped from its ends and from around its dots.
func localPartOf(t *hdrToken) string {
	res := []*hdrToken{pythonDot}
	last := pythonDot
	lastIsList := false
	tokens := append([]*hdrToken{}, t.children[0].children...)
	tokens = append(tokens, pythonDot)
	for _, tok := range tokens {
		if tok.typ == "cfws" {
			continue
		}
		if lastIsList && tok.typ == "dot" && len(last.children) > 0 &&
			last.children[len(last.children)-1].typ == "cfws" {
			res[len(res)-1] = newTokenList("list", last.children[:len(last.children)-1]...)
		}
		isList := !tok.terminal
		if isList && last.typ == "dot" && len(tok.children) > 0 && tok.children[0].typ == "cfws" {
			res = append(res, newTokenList("list", tok.children[1:]...))
		} else {
			res = append(res, tok)
		}
		last = res[len(res)-1]
		lastIsList = isList
	}
	return joinTokenValues(res[1 : len(res)-1])
}

// ---------------------------------------------------------------------------
// The parser functions of email._header_value_parser
// ---------------------------------------------------------------------------

// splitWSP is re.split("([ \t]+)", value, 1): the text before the first
// whitespace run and the rest, which starts with that run.
func splitWSP(value string) (first, rest string) {
	for i := 0; i < len(value); i++ {
		if value[i] != ' ' && value[i] != '\t' {
			continue
		}
		j := i
		for j < len(value) && (value[j] == ' ' || value[j] == '\t') {
			j++
		}
		return value[:i], value[i:]
	}
	return value, ""
}

func isHexDigit(c byte) bool {
	return c >= '0' && c <= '9' || c >= 'a' && c <= 'f' || c >= 'A' && c <= 'F'
}

// getFWS is get_fws: FWS = 1*WSP, already unfolded.
func getFWS(value string) (*hdrToken, string) {
	stripped := pyLStrip(value)
	return newTerminal("fws", value[:len(value)-len(stripped)]), stripped
}

// getTextToEndchars is _get_ptext_to_endchars: the unquoted text before one
// of endchars, the remaining value, and whether a quoted pair was decoded.
func getTextToEndchars(value, endchars string) (text, rest string, hadQP bool) {
	if value == "" {
		return "", "", false
	}
	fragment, remainder := splitWSP(value)
	escape := false
	var chars []byte
	pos := len(fragment)
	for i := 0; i < len(fragment); i++ {
		c := fragment[i]
		if c == '\\' {
			if escape {
				escape = false
				hadQP = true
			} else {
				escape = true
				continue
			}
		}
		if escape {
			escape = false
		} else if strings.IndexByte(endchars, c) >= 0 {
			pos = i
			break
		}
		chars = append(chars, c)
	}
	return string(chars), fragment[pos:] + remainder, hadQP
}

// getQPCText is get_qp_ctext: ctext with quoted pairs decoded.
func getQPCText(value string) (*hdrToken, string) {
	text, rest, _ := getTextToEndchars(value, "()")
	return newTerminal("ptext", text), rest
}

// getQContent is get_qcontent.
func getQContent(value string) (*hdrToken, string) {
	text, rest, _ := getTextToEndchars(value, `"`)
	return newTerminal("ptext", text), rest
}

// getDText is get_dtext.
func getDText(value string) (*hdrToken, string) {
	text, rest, _ := getTextToEndchars(value, "[]")
	return newTerminal("ptext", text), rest
}

// getAText is get_atext.
func getAText(value string) (*hdrToken, string, error) {
	end := 0
	for end < len(value) && strings.IndexByte(atomEnds, value[end]) < 0 {
		end++
	}
	if end == 0 {
		return nil, "", headerParsef("expected atext but found '%s'", value)
	}
	return newTerminal("atext", value[:end]), value[end:], nil
}

// getComment is get_comment.
func getComment(value string) (*hdrToken, string, error) {
	if value != "" && value[0] != '(' {
		return nil, "", headerParsef("expected '(' but found '%s'", value)
	}
	comment := newTokenList("comment")
	if value != "" {
		value = value[1:]
	}
	for value != "" && value[0] != ')' {
		var token *hdrToken
		var err error
		switch value[0] {
		case ' ', '\t':
			token, value = getFWS(value)
		case '(':
			token, value, err = getComment(value)
			if err != nil {
				return nil, "", err
			}
		default:
			token, value = getQPCText(value)
		}
		comment.children = append(comment.children, token)
	}
	if value == "" {
		return comment, value, nil
	}
	return comment, value[1:], nil
}

// getCFWS is get_cfws.
func getCFWS(value string) (*hdrToken, string) {
	cfws := newTokenList("cfws")
	for value != "" && strings.IndexByte(cfwsLeader, value[0]) >= 0 {
		var token *hdrToken
		if value[0] == ' ' || value[0] == '\t' {
			token, value = getFWS(value)
		} else {
			// getComment only fails when the value does not start with '(',
			// which the loop condition rules out.
			token, value, _ = getComment(value)
		}
		cfws.children = append(cfws.children, token)
	}
	return cfws, value
}

// getBareQuotedString is get_bare_quoted_string.
func getBareQuotedString(value string) (*hdrToken, string, error) {
	if value == "" || value[0] != '"' {
		return nil, "", headerParsef("expected '\"' but found '%s'", value)
	}
	bare := newTokenList("bare-quoted-string")
	value = value[1:]
	if value != "" && value[0] == '"' {
		return bare, value[1:], nil
	}
	for value != "" && value[0] != '"' {
		var token *hdrToken
		if value[0] == ' ' || value[0] == '\t' {
			token, value = getFWS(value)
		} else if strings.HasPrefix(value, "=?") {
			var rest string
			var err error
			token, rest, err = getEncodedWord(value, "vtext")
			validEW := err == nil
			if err != nil {
				if !isHeaderParseError(err) {
					return nil, "", err
				}
				token, rest = getQContent(value)
			}
			value = rest
			// Collapse the whitespace between two encoded words.
			if validEW && len(bare.children) > 1 {
				n := len(bare.children)
				if bare.children[n-1].typ == "fws" && bare.children[n-2].typ == "encoded-word" {
					bare.children[n-1] = newTerminal("ew-fws", "")
				}
			}
		} else {
			token, value = getQContent(value)
		}
		bare.children = append(bare.children, token)
	}
	if value == "" {
		return bare, value, nil
	}
	return bare, value[1:], nil
}

// getQuotedString is get_quoted_string.
func getQuotedString(value string) (*hdrToken, string, error) {
	quoted := newTokenList("quoted-string")
	if value != "" && strings.IndexByte(cfwsLeader, value[0]) >= 0 {
		token, rest := getCFWS(value)
		quoted.children = append(quoted.children, token)
		value = rest
	}
	token, rest, err := getBareQuotedString(value)
	if err != nil {
		return nil, "", err
	}
	quoted.children = append(quoted.children, token)
	value = rest
	if value != "" && strings.IndexByte(cfwsLeader, value[0]) >= 0 {
		token, value = getCFWS(value)
		quoted.children = append(quoted.children, token)
	}
	return quoted, value, nil
}

// getAtom is get_atom.
func getAtom(value string) (*hdrToken, string, error) {
	atom := newTokenList("atom")
	if value != "" && strings.IndexByte(cfwsLeader, value[0]) >= 0 {
		token, rest := getCFWS(value)
		atom.children = append(atom.children, token)
		value = rest
	}
	if value != "" && strings.IndexByte(atomEnds, value[0]) >= 0 {
		return nil, "", headerParsef("expected atom but found '%s'", value)
	}
	var token *hdrToken
	var rest string
	var err error
	if strings.HasPrefix(value, "=?") {
		token, rest, err = getEncodedWord(value, "vtext")
		if err != nil {
			if !isHeaderParseError(err) {
				return nil, "", err
			}
			token, rest, err = getAText(value)
		}
	} else {
		token, rest, err = getAText(value)
	}
	value = rest
	if err != nil {
		return nil, "", err
	}
	atom.children = append(atom.children, token)
	if value != "" && strings.IndexByte(cfwsLeader, value[0]) >= 0 {
		token, value = getCFWS(value)
		atom.children = append(atom.children, token)
	}
	return atom, value, nil
}

// getDotAtomText is get_dot_atom_text.
func getDotAtomText(value string) (*hdrToken, string, error) {
	text := newTokenList("dot-atom-text")
	if value == "" || strings.IndexByte(atomEnds, value[0]) >= 0 {
		return nil, "", headerParsef("expected atom at a start of dot-atom-text but found '%s'", value)
	}
	lastIsDot := false
	for value != "" && strings.IndexByte(atomEnds, value[0]) < 0 {
		token, rest, err := getAText(value)
		if err != nil {
			return nil, "", err
		}
		text.children = append(text.children, token)
		value = rest
		lastIsDot = false
		if value != "" && value[0] == '.' {
			text.children = append(text.children, pythonDot)
			value = value[1:]
			lastIsDot = true
		}
	}
	if lastIsDot {
		return nil, "", headerParsef("expected atom at end of dot-atom-text but found '.%s'", value)
	}
	return text, value, nil
}

// getDotAtom is get_dot_atom.
func getDotAtom(value string) (*hdrToken, string, error) {
	dotAtom := newTokenList("dot-atom")
	if value != "" && strings.IndexByte(cfwsLeader, value[0]) >= 0 {
		token, rest := getCFWS(value)
		dotAtom.children = append(dotAtom.children, token)
		value = rest
	}
	var token *hdrToken
	var rest string
	var err error
	if strings.HasPrefix(value, "=?") {
		token, rest, err = getEncodedWord(value, "vtext")
		if err != nil {
			if !isHeaderParseError(err) {
				return nil, "", err
			}
			token, rest, err = getDotAtomText(value)
		}
	} else {
		token, rest, err = getDotAtomText(value)
	}
	value = rest
	if err != nil {
		return nil, "", err
	}
	dotAtom.children = append(dotAtom.children, token)
	if value != "" && strings.IndexByte(cfwsLeader, value[0]) >= 0 {
		token, value = getCFWS(value)
		dotAtom.children = append(dotAtom.children, token)
	}
	return dotAtom, value, nil
}

// getWord is get_word.
func getWord(value string) (*hdrToken, string, error) {
	var leader *hdrToken
	if value != "" && strings.IndexByte(cfwsLeader, value[0]) >= 0 {
		leader, value = getCFWS(value)
	}
	if value == "" {
		return nil, "", headerParsef("Expected 'atom' or 'quoted-string' but found nothing.")
	}
	var token *hdrToken
	var err error
	switch {
	case value[0] == '"':
		token, value, err = getQuotedString(value)
	case strings.IndexByte(headerSpecs, value[0]) >= 0:
		return nil, "", headerParsef("Expected 'atom' or 'quoted-string' but found '%s'", value)
	default:
		token, value, err = getAtom(value)
	}
	if err != nil {
		return nil, "", err
	}
	if leader != nil {
		token.children = append([]*hdrToken{leader}, token.children...)
	}
	return token, value, nil
}

// getPhrase is get_phrase.
func getPhrase(value string) (*hdrToken, string, error) {
	phrase := newTokenList("phrase")
	if value == "" {
		return nil, "", errHeaderIndex
	}
	if token, rest, err := getWord(value); err == nil {
		phrase.children = append(phrase.children, token)
		value = rest
	} else if !isHeaderParseError(err) {
		return nil, "", err
	}
	for value != "" && strings.IndexByte(phraseEnds, value[0]) < 0 {
		if value[0] == '.' {
			phrase.children = append(phrase.children, pythonDot)
			value = value[1:]
			continue
		}
		token, rest, err := getWord(value)
		if err != nil {
			if !isHeaderParseError(err) {
				return nil, "", err
			}
			if strings.IndexByte(cfwsLeader, value[0]) < 0 {
				return nil, "", err
			}
			token, rest = getCFWS(value)
		}
		if len(token.children) > 0 && token.children[0].typ == "encoded-word" &&
			len(phrase.children) > 0 {
			prev := phrase.children[len(phrase.children)-1]
			if prev.typ == "atom" && len(prev.children) > 1 &&
				prev.children[len(prev.children)-2].typ == "encoded-word" &&
				prev.children[len(prev.children)-1].typ == "cfws" &&
				!hasCommentChild(prev.children[len(prev.children)-1]) {
				// Linear whitespace between encoded words collapses.
				prev.children[len(prev.children)-1] = newTerminal("ew-fws", "")
			}
		}
		phrase.children = append(phrase.children, token)
		value = rest
	}
	return phrase, value, nil
}

// getEncodedWord is get_encoded_word.
func getEncodedWord(value, terminalType string) (*hdrToken, string, error) {
	word := newTokenList("encoded-word")
	if !strings.HasPrefix(value, "=?") {
		return nil, "", headerParsef("expected encoded word but found %s", value)
	}
	tail := value[2:]
	index := strings.Index(tail, "?=")
	if index < 0 {
		return nil, "", headerParsef("expected encoded word but found %s", value)
	}
	tok := tail[:index]
	remainder := tail[index+2:]
	if len(remainder) > 1 && isHexDigit(remainder[0]) && isHexDigit(remainder[1]) &&
		strings.Count(tok, "?") < 2 {
		// The '?' after the CTE was followed by an encoded word escape (=XX).
		rest := remainder
		if next := strings.Index(remainder, "?="); next >= 0 {
			rest, remainder = remainder[:next], remainder[next+2:]
		} else {
			remainder = ""
		}
		tok = tok + "?=" + rest
	}
	value = remainder
	text, err := decodeEncodedWord("=?" + tok + "?=")
	if err != nil {
		return nil, "", err
	}
	for text != "" {
		if text[0] == ' ' || text[0] == '\t' {
			token, rest := getFWS(text)
			word.children = append(word.children, token)
			text = rest
			continue
		}
		chars, rest := splitWSP(text)
		word.children = append(word.children, newTerminal(terminalType, chars))
		text = rest
	}
	return word, value, nil
}

// getLocalPart is get_local_part.
func getLocalPart(value string) (*hdrToken, string, error) {
	localPart := newTokenList("local-part")
	var leader *hdrToken
	if value != "" && strings.IndexByte(cfwsLeader, value[0]) >= 0 {
		leader, value = getCFWS(value)
	}
	if value == "" {
		return nil, "", headerParsef("expected local-part but found '%s'", value)
	}
	token, rest, err := getDotAtom(value)
	if err != nil {
		if !isHeaderParseError(err) {
			return nil, "", err
		}
		if token, rest, err = getWord(value); err != nil {
			if !isHeaderParseError(err) {
				return nil, "", err
			}
			if value[0] != '\\' && strings.IndexByte(phraseEnds, value[0]) >= 0 {
				return nil, "", err
			}
			token, rest = newTokenList("list"), value
		}
	}
	value = rest
	if leader != nil {
		token.children = append([]*hdrToken{leader}, token.children...)
	}
	localPart.children = append(localPart.children, token)
	if value != "" && (value[0] == '\\' || strings.IndexByte(phraseEnds, value[0]) < 0) {
		observed, rest, err := getObsLocalPart(localPart.str() + value)
		if err != nil {
			return nil, "", err
		}
		localPart.children[0] = observed
		value = rest
	}
	return localPart, value, nil
}

// getObsLocalPart is get_obs_local_part.
func getObsLocalPart(value string) (*hdrToken, string, error) {
	obs := newTokenList("obs-local-part")
	for value != "" && (value[0] == '\\' || strings.IndexByte(phraseEnds, value[0]) < 0) {
		if value[0] == '.' {
			obs.children = append(obs.children, pythonDot)
			value = value[1:]
			continue
		}
		if value[0] == '\\' {
			obs.children = append(obs.children, newTerminal("misplaced-special", value[:1]))
			value = value[1:]
			continue
		}
		token, rest, err := getWord(value)
		if err != nil {
			if !isHeaderParseError(err) {
				return nil, "", err
			}
			if strings.IndexByte(cfwsLeader, value[0]) < 0 {
				return nil, "", err
			}
			token, rest = getCFWS(value)
		}
		obs.children = append(obs.children, token)
		value = rest
	}
	if len(obs.children) == 0 {
		return nil, "", headerParsef("expected obs-local-part but found '%s'", value)
	}
	return obs, value, nil
}

// checkEarlyDomainLiteralEnd is _check_for_early_dl_end.
func checkEarlyDomainLiteralEnd(value string, literal *hdrToken) bool {
	if value != "" {
		return false
	}
	literal.children = append(literal.children, newTerminal("domain-literal-end", "]"))
	return true
}

// getDomainLiteral is get_domain_literal.
func getDomainLiteral(value string) (*hdrToken, string, error) {
	literal := newTokenList("domain-literal")
	if value != "" && strings.IndexByte(cfwsLeader, value[0]) >= 0 {
		token, rest := getCFWS(value)
		literal.children = append(literal.children, token)
		value = rest
	}
	if value == "" {
		return nil, "", headerParsef("expected domain-literal")
	}
	if value[0] != '[' {
		return nil, "", headerParsef("expected '[' at start of domain-literal but found '%s'", value)
	}
	value = value[1:]
	literal.children = append(literal.children, newTerminal("domain-literal-start", "["))
	if checkEarlyDomainLiteralEnd(value, literal) {
		return literal, value, nil
	}
	if value[0] == ' ' || value[0] == '\t' {
		token, rest := getFWS(value)
		literal.children = append(literal.children, token)
		value = rest
	}
	token, rest := getDText(value)
	literal.children = append(literal.children, token)
	value = rest
	if checkEarlyDomainLiteralEnd(value, literal) {
		return literal, value, nil
	}
	if value[0] == ' ' || value[0] == '\t' {
		token, rest = getFWS(value)
		literal.children = append(literal.children, token)
		value = rest
	}
	if checkEarlyDomainLiteralEnd(value, literal) {
		return literal, value, nil
	}
	if value[0] != ']' {
		return nil, "", headerParsef("expected ']' at end of domain-literal but found '%s'", value)
	}
	literal.children = append(literal.children, newTerminal("domain-literal-end", "]"))
	value = value[1:]
	if value != "" && strings.IndexByte(cfwsLeader, value[0]) >= 0 {
		token, value = getCFWS(value)
		literal.children = append(literal.children, token)
	}
	return literal, value, nil
}

// getDomain is get_domain.
func getDomain(value string) (*hdrToken, string, error) {
	domain := newTokenList("domain")
	var leader *hdrToken
	if value != "" && strings.IndexByte(cfwsLeader, value[0]) >= 0 {
		leader, value = getCFWS(value)
	}
	if value == "" {
		return nil, "", headerParsef("expected domain but found '%s'", value)
	}
	if value[0] == '[' {
		token, rest, err := getDomainLiteral(value)
		if err != nil {
			return nil, "", err
		}
		if leader != nil {
			token.children = append([]*hdrToken{leader}, token.children...)
		}
		domain.children = append(domain.children, token)
		return domain, rest, nil
	}
	token, rest, err := getDotAtom(value)
	if err != nil {
		if !isHeaderParseError(err) {
			return nil, "", err
		}
		if token, rest, err = getAtom(value); err != nil {
			return nil, "", err
		}
	}
	value = rest
	if value != "" && value[0] == '@' {
		return nil, "", headerParsef("Invalid Domain")
	}
	if leader != nil {
		token.children = append([]*hdrToken{leader}, token.children...)
	}
	domain.children = append(domain.children, token)
	if value != "" && value[0] == '.' {
		if domain.children[0].typ == "dot-atom" {
			domain.children = domain.children[0].children
		}
		for value != "" && value[0] == '.' {
			domain.children = append(domain.children, pythonDot)
			token, rest, err := getAtom(value[1:])
			if err != nil {
				return nil, "", err
			}
			domain.children = append(domain.children, token)
			value = rest
		}
	}
	return domain, value, nil
}

// getAddrSpec is get_addr_spec.
func getAddrSpec(value string) (*hdrToken, string, error) {
	spec := newTokenList("addr-spec")
	token, rest, err := getLocalPart(value)
	if err != nil {
		return nil, "", err
	}
	spec.children = append(spec.children, token)
	value = rest
	if value == "" || value[0] != '@' {
		return spec, value, nil
	}
	spec.children = append(spec.children, newTerminal("address-at-symbol", "@"))
	token, value, err = getDomain(value[1:])
	if err != nil {
		return nil, "", err
	}
	spec.children = append(spec.children, token)
	return spec, value, nil
}

// getObsRoute is get_obs_route.
func getObsRoute(value string) (*hdrToken, string, error) {
	route := newTokenList("obs-route")
	for value != "" && (value[0] == ',' || strings.IndexByte(cfwsLeader, value[0]) >= 0) {
		if strings.IndexByte(cfwsLeader, value[0]) >= 0 {
			token, rest := getCFWS(value)
			route.children = append(route.children, token)
			value = rest
		} else {
			route.children = append(route.children, pythonListSeparator)
			value = value[1:]
		}
	}
	if value == "" || value[0] != '@' {
		return nil, "", headerParsef("expected obs-route domain but found '%s'", value)
	}
	route.children = append(route.children, pythonRouteMarker)
	token, rest, err := getDomain(value[1:])
	if err != nil {
		return nil, "", err
	}
	route.children = append(route.children, token)
	value = rest
	for value != "" && value[0] == ',' {
		route.children = append(route.children, pythonListSeparator)
		value = value[1:]
		if value == "" {
			break
		}
		if strings.IndexByte(cfwsLeader, value[0]) >= 0 {
			var cfws *hdrToken
			cfws, value = getCFWS(value)
			route.children = append(route.children, cfws)
		}
		if value == "" {
			break
		}
		if value[0] == '@' {
			route.children = append(route.children, pythonRouteMarker)
			token, value, err = getDomain(value[1:])
			if err != nil {
				return nil, "", err
			}
			route.children = append(route.children, token)
		}
	}
	if value == "" {
		return nil, "", headerParsef("end of header while parsing obs-route")
	}
	if value[0] != ':' {
		return nil, "", headerParsef("expected ':' marking end of obs-route but found '%s'", value)
	}
	route.children = append(route.children, newTerminal("end-of-obs-route-marker", ":"))
	return route, value[1:], nil
}

// getAngleAddr is get_angle_addr.
func getAngleAddr(value string) (*hdrToken, string, error) {
	angle := newTokenList("angle-addr")
	if value != "" && strings.IndexByte(cfwsLeader, value[0]) >= 0 {
		token, rest := getCFWS(value)
		angle.children = append(angle.children, token)
		value = rest
	}
	if value == "" || value[0] != '<' {
		return nil, "", headerParsef("expected angle-addr but found '%s'", value)
	}
	angle.children = append(angle.children, newTerminal("angle-addr-start", "<"))
	value = value[1:]
	if value != "" && value[0] == '>' {
		angle.children = append(angle.children, newTerminal("angle-addr-end", ">"))
		return angle, value[1:], nil
	}
	token, rest, err := getAddrSpec(value)
	if err != nil {
		if !isHeaderParseError(err) {
			return nil, "", err
		}
		if token, rest, err = getObsRoute(value); err != nil {
			return nil, "", headerParsef("expected addr-spec or obs-route but found '%s'", value)
		}
		angle.children = append(angle.children, token)
		token, rest, err = getAddrSpec(rest)
		if err != nil {
			return nil, "", err
		}
	}
	angle.children = append(angle.children, token)
	value = rest
	if value != "" && value[0] == '>' {
		value = value[1:]
	}
	angle.children = append(angle.children, newTerminal("angle-addr-end", ">"))
	if value != "" && strings.IndexByte(cfwsLeader, value[0]) >= 0 {
		token, value = getCFWS(value)
		angle.children = append(angle.children, token)
	}
	return angle, value, nil
}

// getDisplayName is get_display_name.
func getDisplayName(value string) (*hdrToken, string, error) {
	display := newTokenList("display-name")
	token, rest, err := getPhrase(value)
	if err != nil {
		return nil, "", err
	}
	display.children = append(display.children, token.children...)
	return display, rest, nil
}

// getNameAddr is get_name_addr.
func getNameAddr(value string) (*hdrToken, string, error) {
	nameAddr := newTokenList("name-addr")
	var leader *hdrToken
	if value == "" {
		return nil, "", headerParsef("expected name-addr but found '%s'", value)
	}
	if strings.IndexByte(cfwsLeader, value[0]) >= 0 {
		leader, value = getCFWS(value)
		if value == "" {
			return nil, "", headerParsef("expected name-addr but found '%s'", leader.str())
		}
	}
	if value[0] != '<' {
		if strings.IndexByte(phraseEnds, value[0]) >= 0 {
			return nil, "", headerParsef("expected name-addr but found '%s'", value)
		}
		token, rest, err := getDisplayName(value)
		if err != nil {
			return nil, "", err
		}
		value = rest
		if value == "" {
			return nil, "", headerParsef("expected name-addr but found '%s'", token.str())
		}
		if leader != nil {
			if len(token.children) == 0 {
				return nil, "", errHeaderIndex
			}
			if !token.children[0].terminal {
				token.children[0].children = append([]*hdrToken{leader}, token.children[0].children...)
			} else {
				token.children = append([]*hdrToken{leader}, token.children...)
			}
			leader = nil
		}
		nameAddr.children = append(nameAddr.children, token)
	}
	token, rest, err := getAngleAddr(value)
	if err != nil {
		return nil, "", err
	}
	if leader != nil {
		token.children = append([]*hdrToken{leader}, token.children...)
	}
	nameAddr.children = append(nameAddr.children, token)
	return nameAddr, rest, nil
}

// getMailbox is get_mailbox.
func getMailbox(value string) (*hdrToken, string, error) {
	mailbox := newTokenList("mailbox")
	token, rest, err := getNameAddr(value)
	if err != nil {
		if !isHeaderParseError(err) {
			return nil, "", err
		}
		if token, rest, err = getAddrSpec(value); err != nil {
			return nil, "", headerParsef("expected mailbox but found '%s'", value)
		}
	}
	mailbox.children = append(mailbox.children, token)
	return mailbox, rest, nil
}

// mailboxDisplayName is Mailbox.display_name for the tree getMailbox builds.
func mailboxDisplayName(mailbox *hdrToken) (*string, error) {
	if len(mailbox.children) == 0 || mailbox.children[0].typ != "name-addr" {
		return nil, nil
	}
	nameAddr := mailbox.children[0]
	if len(nameAddr.children) == 1 {
		return nil, nil
	}
	return displayNameOf(nameAddr.children[0])
}

// mailboxLocalPart is Mailbox.local_part.
func mailboxLocalPart(mailbox *hdrToken) (*string, error) {
	token := mailbox.children[0]
	if token.typ == "name-addr" {
		token = token.children[len(token.children)-1]
	}
	if token.typ == "angle-addr" {
		for _, child := range token.children {
			if child.typ == "addr-spec" {
				token = child
				break
			}
		}
	}
	if token.typ != "addr-spec" || len(token.children) == 0 {
		return nil, nil
	}
	local := token.children[0]
	if local.typ != "local-part" || len(local.children) == 0 {
		return nil, nil
	}
	return ptrTo(localPartOf(local)), nil
}

// mailboxDomain is Mailbox.domain.
func mailboxDomain(mailbox *hdrToken) (*string, error) {
	token := mailbox.children[0]
	if token.typ == "name-addr" {
		token = token.children[len(token.children)-1]
	}
	if token.typ == "angle-addr" {
		for _, child := range token.children {
			if child.typ == "addr-spec" {
				token = child
				break
			}
		}
	}
	if token.typ != "addr-spec" || len(token.children) < 3 {
		return nil, nil
	}
	return ptrTo(token.children[len(token.children)-1].value()), nil
}

// ---------------------------------------------------------------------------
// Encoded words: email._encoded_words.decode
// ---------------------------------------------------------------------------

// errInvalidEncodedWord is the _InvalidEwError get_encoded_word raises.
var errInvalidEncodedWord = headerParsef("encoded word format invalid")

// decodeEncodedWord is email._encoded_words.decode for one encoded word: the
// decoded text. Charsets are decoded as UTF-8, US-ASCII and ISO-8859-1 (the
// ones addresses carry); every other name takes Python's unknown-charset path.
func decodeEncodedWord(word string) (string, error) {
	parts := strings.Split(word, "?")
	if len(parts) != 5 {
		return "", errInvalidEncodedWord
	}
	charset, _, _ := strings.Cut(parts[1], "*")
	cte := strings.ToLower(parts[2])
	encoded := parts[3]
	raw, ok := asciiSurrogateescape(encoded)
	if !ok {
		return "", fmt.Errorf("%w: encoded word with non-ASCII text", ErrRaises)
	}
	switch cte {
	case "q":
		raw = decodeQ(raw)
	case "b":
		decoded, err := decodeB(raw)
		if err != nil {
			return "", err
		}
		raw = decoded
	default:
		return "", errInvalidEncodedWord
	}
	return decodeCharset(raw, charset)
}

// asciiSurrogateescape is s.encode('ascii', 'surrogateescape').
func asciiSurrogateescape(s string) ([]byte, bool) {
	out := make([]byte, 0, len(s))
	for _, r := range pystr.Runes(s) {
		switch {
		case r < 0x80:
			out = append(out, byte(r))
		case r >= 0xDC80 && r <= 0xDCFF:
			out = append(out, byte(r-0xDC00))
		default:
			return nil, false
		}
	}
	return out, true
}

// decodeQ is _encoded_words.decode_q.
func decodeQ(encoded []byte) []byte {
	out := make([]byte, 0, len(encoded))
	for i := 0; i < len(encoded); i++ {
		if encoded[i] == '_' {
			out = append(out, ' ')
			continue
		}
		if encoded[i] == '=' && i+2 < len(encoded) && isHexDigit(encoded[i+1]) && isHexDigit(encoded[i+2]) {
			out = append(out, hexByte(encoded[i+1])<<4|hexByte(encoded[i+2]))
			i += 2
			continue
		}
		out = append(out, encoded[i])
	}
	return out
}

func hexByte(c byte) byte {
	switch {
	case c >= '0' && c <= '9':
		return c - '0'
	case c >= 'a' && c <= 'f':
		return c - 'a' + 10
	}
	return c - 'A' + 10
}

// decodeB is _encoded_words.decode_b: the strict decode with the padding
// fixed, then Python's own lenient base64 decode.
func decodeB(encoded []byte) ([]byte, error) {
	padErr := len(encoded) % 4
	padded := append(append([]byte{}, encoded...), []byte(strings.Repeat("=", (4-padErr)%4))...)
	if padErr == 0 {
		padded = encoded
	}
	if decoded, err := base64.StdEncoding.Strict().DecodeString(string(padded)); err == nil {
		return decoded, nil
	}
	decoded, err := pythonB64Decode(encoded)
	if err == nil {
		return decoded, nil
	}
	padded = append(append([]byte{}, encoded...), '=', '=')
	if decoded, err = pythonB64Decode(padded); err == nil {
		return decoded, nil
	}
	// bpo-27397: no way to decode, the encoded text is returned as is.
	return encoded, nil
}

// decodeCharset is the bstring.decode(charset) of _encoded_words.decode.
func decodeCharset(raw []byte, charset string) (string, error) {
	switch pythonCodecName(charset) {
	case "utf-8":
		return decodeUTF8Surrogateescape(raw), nil
	case "ascii":
		return decodeASCIISurrogateescape(raw), nil
	case "iso8859-1":
		var out strings.Builder
		for _, b := range raw {
			out.WriteRune(rune(b))
		}
		return out.String(), nil
	case "":
		// LookupError (an unknown name, unknown-8bit, or a bytes-to-bytes
		// codec): the bytes are decoded as unknown bytes.
		return decodeASCIISurrogateescape(raw), nil
	}
	// Python decodes any other text codec of its registry (gb2312,
	// shift_jis, koi8-r, utf-16, ...); those codecs are not ported, so such a
	// word stays with Django.
	return "", ErrEncodedWordCharset
}

// pythonCodecName is codecs.lookup(charset).name for a text encoding that
// bytes.decode accepts, "" where it raises LookupError: the name lowered,
// encodings.normalize_encoding, then the alias table (also with '.' as '_')
// or a codec module of that name (python_codecs.go).
func pythonCodecName(charset string) string {
	var normalized strings.Builder
	punct := false
	for _, r := range charset {
		if r >= 'A' && r <= 'Z' {
			r += 'a' - 'A'
		}
		if r == '.' || unicode.IsLetter(r) || unicode.IsNumber(r) {
			if punct && normalized.Len() > 0 {
				normalized.WriteByte('_')
			}
			if r < utf8.RuneSelf {
				normalized.WriteRune(r)
			}
			punct = false
			continue
		}
		punct = true
	}
	name := normalized.String()
	if codec, ok := pythonTextCodecs[name]; ok {
		return codec
	}
	return pythonTextCodecs[strings.ReplaceAll(name, ".", "_")]
}

// ErrEncodedWordCharset refuses an RFC 2047 encoded word in an address whose
// charset is not UTF-8, US-ASCII or ISO-8859-1: Python decodes it with its
// codec, which this package does not carry.
var ErrEncodedWordCharset = refusal.New("smtpsend: encoded-word charset not ported")

// decodeUTF8Surrogateescape is b.decode('utf-8', 'surrogateescape').
func decodeUTF8Surrogateescape(raw []byte) string {
	runes := make([]rune, 0, len(raw))
	for i := 0; i < len(raw); {
		if raw[i] < 0x80 {
			runes = append(runes, rune(raw[i]))
			i++
			continue
		}
		r, size := decodeRuneStrict(raw[i:])
		if size == 0 {
			runes = append(runes, 0xDC00+rune(raw[i]))
			i++
			continue
		}
		runes = append(runes, r)
		i += size
	}
	return pystr.String(runes)
}

// decodeASCIISurrogateescape is b.decode('ascii', 'surrogateescape').
func decodeASCIISurrogateescape(raw []byte) string {
	runes := make([]rune, 0, len(raw))
	for _, b := range raw {
		if b < 0x80 {
			runes = append(runes, rune(b))
		} else {
			runes = append(runes, 0xDC00+rune(b))
		}
	}
	return pystr.String(runes)
}

// hasCommentChild reports whether a whitespace token holds a comment, the
// comments property get_phrase checks before it collapses the whitespace
// between two encoded words.
func hasCommentChild(t *hdrToken) bool {
	for _, child := range t.children {
		if child.typ == "comment" {
			return true
		}
	}
	return false
}

// isHeaderParseError reports the errors get_encoded_word and get_word raise,
// which the callers fall back from (a UnicodeEncodeError propagates).
func isHeaderParseError(err error) bool {
	var parseErr *headerParseError
	return errors.As(err, &parseErr)
}

// decodeRuneStrict is the utf-8 codec's strict decode of one character: the
// rune and its size, or size 0 for the first byte of an invalid sequence.
func decodeRuneStrict(raw []byte) (rune, int) {
	if len(raw) == 0 {
		return 0, 0
	}
	r, size := utf8.DecodeRune(raw)
	if r == utf8.RuneError && size <= 1 {
		return 0, 0
	}
	return r, size
}
