package config

import (
	"strings"
	"unicode"
)

// ParseAddr is Python 3.13's email.utils.parseaddr(addr) (strict=True): the
// parenthesis pre-check, email._parseaddr.AddressList and the domain-literal
// post-check; ("", "") unless the field holds exactly one address.
// core.settings builds DEFAULT_FROM_EMAIL as f"{WHITELABEL_NAME} <{EMAIL_HOST_USER}>"
// and email_configuration.effective_email reads it back with parseaddr.
func ParseAddr(addr string) (string, string) {
	if !parseaddrBalanced(addr) {
		addr = "('', '')"
	}
	addresses := (&addrlist{field: []rune(addr)}).list()
	if len(addresses) != 1 {
		return "", ""
	}
	if strings.Contains(addresses[0][1], "[") {
		return "", ""
	}
	return addresses[0][0], addresses[0][1]
}

// parseaddrEscaped is utils._iter_escaped_chars: (position, character or
// backslash pair).
type parseaddrEscaped struct {
	pos  int
	text string
}

func parseaddrIterEscaped(addr []rune) []parseaddrEscaped {
	var out []parseaddrEscaped
	pos, escape := 0, false
	for i, r := range addr {
		pos = i
		switch {
		case escape:
			out = append(out, parseaddrEscaped{i, `\` + string(r)})
			escape = false
		case r == '\\':
			escape = true
		default:
			out = append(out, parseaddrEscaped{i, string(r)})
		}
	}
	if escape {
		out = append(out, parseaddrEscaped{pos, `\`})
	}
	return out
}

// parseaddrStripQuoted is utils._strip_quoted_realnames.
func parseaddrStripQuoted(addr []rune) []rune {
	if !strings.ContainsRune(string(addr), '"') {
		return addr
	}
	start, open := 0, -1
	var result []rune
	for _, item := range parseaddrIterEscaped(addr) {
		if item.text != `"` {
			continue
		}
		if open < 0 {
			open = item.pos
			continue
		}
		if start != open {
			result = append(result, addr[start:open]...)
		}
		start, open = item.pos+1, -1
	}
	if start < len(addr) {
		result = append(result, addr[start:]...)
	}
	return result
}

// parseaddrBalanced is utils._check_parenthesis.
func parseaddrBalanced(addr string) bool {
	opens := 0
	for _, item := range parseaddrIterEscaped(parseaddrStripQuoted([]rune(addr))) {
		switch item.text {
		case "(":
			opens++
		case ")":
			opens--
			if opens < 0 {
				return false
			}
		}
	}
	return opens == 0
}

const (
	parseaddrSpecials = `()<>@,:;."[]`
	parseaddrLWS      = " \t"
	parseaddrCR       = "\r\n"
	parseaddrFWS      = parseaddrLWS + parseaddrCR
	parseaddrAtomEnds = parseaddrSpecials + parseaddrLWS + parseaddrCR
)

// parseaddrPhraseEnds is atomends without '.' (obs-phrase).
var parseaddrPhraseEnds = strings.ReplaceAll(parseaddrAtomEnds, ".", "")

// addrlist is email._parseaddr.AddrlistClass over code points.
type addrlist struct {
	field    []rune
	pos      int
	comments []string
}

func (a *addrlist) at() rune              { return a.field[a.pos] }
func (a *addrlist) more() bool            { return a.pos < len(a.field) }
func parseaddrIn(r rune, set string) bool { return strings.ContainsRune(set, r) }

// parseaddrBlank is not text.strip() (str.isspace: Unicode White_Space and
// the information separators U+001C..U+001F).
func parseaddrBlank(text string) bool {
	for _, r := range text {
		if !unicode.IsSpace(r) && (r < 0x1c || r > 0x1f) {
			return false
		}
	}
	return true
}

func (a *addrlist) gotonext() string {
	var ws []rune
	for a.more() {
		switch r := a.at(); {
		case parseaddrIn(r, parseaddrLWS+"\n\r"):
			if !parseaddrIn(r, "\n\r") {
				ws = append(ws, r)
			}
			a.pos++
		case r == '(':
			a.comments = append(a.comments, a.getcomment())
		default:
			return string(ws)
		}
	}
	return string(ws)
}

func (a *addrlist) list() [][2]string {
	var result [][2]string
	for a.more() {
		if ad := a.getaddress(); len(ad) > 0 {
			result = append(result, ad...)
		} else {
			result = append(result, [2]string{"", ""})
		}
	}
	return result
}

func (a *addrlist) getaddress() [][2]string {
	a.comments = nil
	a.gotonext()
	oldpos, oldcl := a.pos, a.comments
	plist := a.getphraselist()
	a.gotonext()
	var out [][2]string
	switch {
	case !a.more():
		if len(plist) > 0 {
			out = [][2]string{{strings.Join(a.comments, " "), plist[0]}}
		}
	case parseaddrIn(a.at(), ".@"):
		a.pos, a.comments = oldpos, oldcl
		spec := a.getaddrspec()
		out = [][2]string{{strings.Join(a.comments, " "), spec}}
	case a.at() == ':':
		length := len(a.field)
		a.pos++
		for a.more() {
			a.gotonext()
			if a.pos < length && a.at() == ';' {
				a.pos++
				break
			}
			out = append(out, a.getaddress()...)
		}
	case a.at() == '<':
		route := a.getrouteaddr()
		if len(a.comments) > 0 {
			out = [][2]string{{strings.Join(plist, " ") + " (" + strings.Join(a.comments, " ") + ")", route}}
		} else {
			out = [][2]string{{strings.Join(plist, " "), route}}
		}
	default:
		if len(plist) > 0 {
			out = [][2]string{{strings.Join(a.comments, " "), plist[0]}}
		} else if parseaddrIn(a.at(), parseaddrSpecials) {
			a.pos++
		}
	}
	a.gotonext()
	if a.more() && a.at() == ',' {
		a.pos++
	}
	return out
}

func (a *addrlist) getrouteaddr() string {
	if a.at() != '<' {
		return ""
	}
	expectRoute := false
	a.pos++
	a.gotonext()
	adlist := ""
	for a.more() {
		switch {
		case expectRoute:
			a.getdomain()
			expectRoute = false
		case a.at() == '>':
			a.pos++
			return adlist
		case a.at() == '@':
			a.pos++
			expectRoute = true
		case a.at() == ':':
			a.pos++
		default:
			adlist = a.getaddrspec()
			a.pos++
			return adlist
		}
		a.gotonext()
	}
	return adlist
}

// parseaddrQuote is _parseaddr.quote.
func parseaddrQuote(text string) string {
	return strings.ReplaceAll(strings.ReplaceAll(text, `\`, `\\`), `"`, `\"`)
}

func (a *addrlist) getaddrspec() string {
	var parts []string
	a.gotonext()
	for a.more() {
		preserve := true
		r := a.at()
		if r == '.' {
			if len(parts) > 0 && parseaddrBlank(parts[len(parts)-1]) {
				parts = parts[:len(parts)-1]
			}
			parts = append(parts, ".")
			a.pos++
			preserve = false
		} else if r == '"' {
			parts = append(parts, `"`+parseaddrQuote(a.getquote())+`"`)
		} else if parseaddrIn(r, parseaddrAtomEnds) {
			if len(parts) > 0 && parseaddrBlank(parts[len(parts)-1]) {
				parts = parts[:len(parts)-1]
			}
			break
		} else {
			parts = append(parts, a.getatom(parseaddrAtomEnds))
		}
		if ws := a.gotonext(); preserve && ws != "" {
			parts = append(parts, ws)
		}
	}
	if !a.more() || a.at() != '@' {
		return strings.Join(parts, "")
	}
	parts = append(parts, "@")
	a.pos++
	a.gotonext()
	domain := a.getdomain()
	if domain == "" {
		return ""
	}
	return strings.Join(parts, "") + domain
}

func (a *addrlist) getdomain() string {
	var parts []string
	for a.more() {
		switch r := a.at(); {
		case parseaddrIn(r, parseaddrLWS):
			a.pos++
		case r == '(':
			a.comments = append(a.comments, a.getcomment())
		case r == '[':
			parts = append(parts, "["+a.getdelimited('[', "]\r", false)+"]")
		case r == '.':
			a.pos++
			parts = append(parts, ".")
		case r == '@':
			return ""
		case parseaddrIn(r, parseaddrAtomEnds):
			return strings.Join(parts, "")
		default:
			parts = append(parts, a.getatom(parseaddrAtomEnds))
		}
	}
	return strings.Join(parts, "")
}

func (a *addrlist) getdelimited(begin rune, ends string, comments bool) string {
	if a.at() != begin {
		return ""
	}
	var out []string
	quote := false
	a.pos++
	for a.more() {
		r := a.at()
		switch {
		case quote:
			out = append(out, string(r))
			quote = false
		case parseaddrIn(r, ends):
			a.pos++
			return strings.Join(out, "")
		case comments && r == '(':
			out = append(out, a.getcomment())
			continue
		case r == '\\':
			quote = true
		default:
			out = append(out, string(r))
		}
		a.pos++
	}
	return strings.Join(out, "")
}

func (a *addrlist) getquote() string   { return a.getdelimited('"', "\"\r", false) }
func (a *addrlist) getcomment() string { return a.getdelimited('(', ")\r", true) }

func (a *addrlist) getatom(ends string) string {
	start := a.pos
	for a.more() && !parseaddrIn(a.at(), ends) {
		a.pos++
	}
	return string(a.field[start:a.pos])
}

func (a *addrlist) getphraselist() []string {
	var plist []string
	for a.more() {
		switch r := a.at(); {
		case parseaddrIn(r, parseaddrFWS):
			a.pos++
		case r == '"':
			plist = append(plist, a.getquote())
		case r == '(':
			a.comments = append(a.comments, a.getcomment())
		case parseaddrIn(r, parseaddrPhraseEnds):
			return plist
		default:
			plist = append(plist, a.getatom(parseaddrPhraseEnds))
		}
	}
	return plist
}
