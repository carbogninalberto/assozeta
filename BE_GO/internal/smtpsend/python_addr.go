package smtpsend

import (
	"strconv"
	"strings"
	"unicode"
)

// pySpace is str.isspace() for any code point.
func pySpace(r rune) bool {
	switch {
	case r >= '\t' && r <= '\r', r >= 0x1c && r <= 0x20, r == 0x85, r == 0xa0, r == 0x1680, r >= 0x2000 && r <= 0x200a,
		r == 0x2028, r == 0x2029, r == 0x202f, r == 0x205f, r == 0x3000:
		return true
	}
	return false
}

func pyStrip(s string) string { return strings.TrimFunc(s, pySpace) }

const (
	addrParserSpecials = `()<>@,:;."[]`
	addrAtomEnds       = addrParserSpecials + " \t\r\n"
	addrPhraseEnds     = `()<>@,:;"[]` + " \t\r\n"
)

type addrPair struct{ name, addr string }

// addrParser is email._parseaddr.AddrlistClass (Python 3.13). It works on
// bytes: every character it tests is ASCII.
type addrParser struct {
	field    string
	pos      int
	comments []string
}

func (p *addrParser) more() bool { return p.pos < len(p.field) }

func (p *addrParser) gotonext() string {
	var ws []byte
	for p.more() {
		c := p.field[p.pos]
		if c == ' ' || c == '\t' || c == '\n' || c == '\r' {
			if c != '\n' && c != '\r' {
				ws = append(ws, c)
			}
			p.pos++
		} else if c == '(' {
			p.comments = append(p.comments, p.getcomment())
		} else {
			break
		}
	}
	return string(ws)
}

func (p *addrParser) getaddrlist() []addrPair {
	var result []addrPair
	for p.more() {
		if ad := p.getaddress(); len(ad) > 0 {
			result = append(result, ad...)
		} else {
			result = append(result, addrPair{})
		}
	}
	return result
}

func (p *addrParser) getaddress() []addrPair {
	p.comments = nil
	p.gotonext()
	oldpos := p.pos
	plist := p.getphraselist()
	p.gotonext()
	var result []addrPair
	switch {
	case !p.more():
		if len(plist) > 0 {
			result = []addrPair{{strings.Join(p.comments, " "), plist[0]}}
		}
	case p.field[p.pos] == '.' || p.field[p.pos] == '@':
		// The comment list is the same object before and after: comments
		// read so far stay and are read again.
		p.pos = oldpos
		addrspec := p.getaddrspec()
		result = []addrPair{{strings.Join(p.comments, " "), addrspec}}
	case p.field[p.pos] == ':':
		p.pos++
		for p.more() {
			p.gotonext()
			if p.more() && p.field[p.pos] == ';' {
				p.pos++
				break
			}
			result = append(result, p.getaddress()...)
		}
	case p.field[p.pos] == '<':
		route := p.getrouteaddr()
		name := strings.Join(plist, " ")
		if len(p.comments) > 0 {
			name += " (" + strings.Join(p.comments, " ") + ")"
		}
		result = []addrPair{{name, route}}
	default:
		if len(plist) > 0 {
			result = []addrPair{{strings.Join(p.comments, " "), plist[0]}}
		} else if strings.IndexByte(addrParserSpecials, p.field[p.pos]) >= 0 {
			p.pos++
		}
	}
	p.gotonext()
	if p.more() && p.field[p.pos] == ',' {
		p.pos++
	}
	return result
}

func (p *addrParser) getrouteaddr() string {
	expectRoute := false
	p.pos++
	p.gotonext()
	adlist := ""
	for p.more() {
		c := p.field[p.pos]
		switch {
		case expectRoute:
			p.getdomain()
			expectRoute = false
		case c == '>':
			p.pos++
			return adlist
		case c == '@':
			p.pos++
			expectRoute = true
		case c == ':':
			p.pos++
		default:
			adlist = p.getaddrspec()
			p.pos++
			return adlist
		}
		p.gotonext()
	}
	return adlist
}

func (p *addrParser) getaddrspec() string {
	var aslist []string
	popBlank := func() {
		if n := len(aslist); n > 0 && pyStrip(aslist[n-1]) == "" {
			aslist = aslist[:n-1]
		}
	}
	p.gotonext()
loop:
	for p.more() {
		preserve := true
		c := p.field[p.pos]
		switch {
		case c == '.':
			popBlank()
			aslist = append(aslist, ".")
			p.pos++
			preserve = false
		case c == '"':
			quoted := p.getquote()
			quoted = strings.ReplaceAll(strings.ReplaceAll(quoted, `\`, `\\`), `"`, `\"`)
			aslist = append(aslist, `"`+quoted+`"`)
		case strings.IndexByte(addrAtomEnds, c) >= 0:
			popBlank()
			break loop
		default:
			aslist = append(aslist, p.getatom(addrAtomEnds))
		}
		if ws := p.gotonext(); preserve && ws != "" {
			aslist = append(aslist, ws)
		}
	}
	if !p.more() || p.field[p.pos] != '@' {
		return strings.Join(aslist, "")
	}
	aslist = append(aslist, "@")
	p.pos++
	p.gotonext()
	domain := p.getdomain()
	if domain == "" {
		return ""
	}
	return strings.Join(aslist, "") + domain
}

func (p *addrParser) getdomain() string {
	var sdlist []string
loop:
	for p.more() {
		c := p.field[p.pos]
		switch {
		case c == ' ' || c == '\t':
			p.pos++
		case c == '(':
			p.comments = append(p.comments, p.getcomment())
		case c == '[':
			sdlist = append(sdlist, "["+p.getdelimited('[', "]\r", false)+"]")
		case c == '.':
			p.pos++
			sdlist = append(sdlist, ".")
		case c == '@':
			return ""
		case strings.IndexByte(addrAtomEnds, c) >= 0:
			break loop
		default:
			sdlist = append(sdlist, p.getatom(addrAtomEnds))
		}
	}
	return strings.Join(sdlist, "")
}

func (p *addrParser) getdelimited(begin byte, ends string, comments bool) string {
	if p.field[p.pos] != begin {
		return ""
	}
	var out []byte
	quote := false
	p.pos++
	for p.more() {
		c := p.field[p.pos]
		switch {
		case quote:
			out = append(out, c)
			quote = false
		case strings.IndexByte(ends, c) >= 0:
			p.pos++
			return string(out)
		case comments && c == '(':
			out = append(out, p.getcomment()...)
			continue
		case c == '\\':
			quote = true
		default:
			out = append(out, c)
		}
		p.pos++
	}
	return string(out)
}

func (p *addrParser) getquote() string   { return p.getdelimited('"', "\"\r", false) }
func (p *addrParser) getcomment() string { return p.getdelimited('(', ")\r", true) }

func (p *addrParser) getatom(ends string) string {
	start := p.pos
	for p.more() && strings.IndexByte(ends, p.field[p.pos]) < 0 {
		p.pos++
	}
	return p.field[start:p.pos]
}

func (p *addrParser) getphraselist() []string {
	var plist []string
loop:
	for p.more() {
		c := p.field[p.pos]
		switch {
		case c == ' ' || c == '\t' || c == '\r' || c == '\n':
			p.pos++
		case c == '"':
			plist = append(plist, p.getquote())
		case c == '(':
			p.comments = append(p.comments, p.getcomment())
		case strings.IndexByte(addrPhraseEnds, c) >= 0:
			break loop
		default:
			plist = append(plist, p.getatom(addrPhraseEnds))
		}
	}
	return plist
}

// stripQuotedRealnames is email.utils._strip_quoted_realnames.
func stripQuotedRealnames(addr string) string {
	if !strings.Contains(addr, `"`) {
		return addr
	}
	start, open, escape := 0, -1, false
	var out strings.Builder
	for pos := 0; pos < len(addr); pos++ {
		switch ch := addr[pos]; {
		case escape:
			escape = false
		case ch == '\\':
			escape = true
		case ch == '"':
			if open < 0 {
				open = pos
			} else {
				if start != open {
					out.WriteString(addr[start:open])
				}
				start, open = pos+1, -1
			}
		}
	}
	if start < len(addr) {
		out.WriteString(addr[start:])
	}
	return out.String()
}

// checkParenthesis is email.utils._check_parenthesis.
func checkParenthesis(addr string) bool {
	addr = stripQuotedRealnames(addr)
	opens, escape := 0, false
	for pos := 0; pos < len(addr); pos++ {
		switch ch := addr[pos]; {
		case escape:
			escape = false
		case ch == '\\':
			escape = true
		case ch == '(':
			opens++
		case ch == ')':
			if opens--; opens < 0 {
				return false
			}
		}
	}
	return opens == 0
}

// parseaddr is email.utils.parseaddr(addr) (strict).
func parseaddr(addr string) (string, string) {
	if !checkParenthesis(addr) {
		addr = "('', '')"
	}
	addrs := (&addrParser{field: addr}).getaddrlist()
	if len(addrs) != 1 || strings.Contains(addrs[0].addr, "[") {
		return "", ""
	}
	return addrs[0].name, addrs[0].addr
}

// QuoteAddr is smtplib.quoteaddr(address) of Python 3.13 for any text.
func QuoteAddr(address string) string {
	name, addr := parseaddr(address)
	if name == "" && addr == "" {
		if strings.HasPrefix(pyStrip(address), "<") {
			return address
		}
		return "<" + address + ">"
	}
	return "<" + addr + ">"
}

// asciiEncodeFailure is the UnicodeEncodeError s.encode('ascii') raises, with
// Python's text; nil for ASCII text.
func asciiEncodeFailure(s string) error {
	position := 0
	runes := []rune(s)
	for position < len(runes) && runes[position] < 0x80 {
		position++
	}
	if position == len(runes) {
		return nil
	}
	end := position
	for end < len(runes) && runes[end] >= 0x80 {
		end++
	}
	if end-position == 1 {
		return failureText("UnicodeEncodeError", "'ascii' codec can't encode character '"+pyEscapeRune(runes[position])+
			"' in position "+strconv.Itoa(position)+": ordinal not in range(128)")
	}
	return failureText("UnicodeEncodeError", "'ascii' codec can't encode characters in position "+strconv.Itoa(position)+"-"+
		strconv.Itoa(end-1)+": ordinal not in range(128)")
}

func pyEscapeRune(r rune) string {
	hex := strconv.FormatInt(int64(r), 16)
	switch {
	case r <= 0xff:
		return `\x` + strings.Repeat("0", 2-len(hex)) + hex
	case r <= 0xffff:
		return `\u` + strings.Repeat("0", 4-len(hex)) + hex
	}
	return `\U` + strings.Repeat("0", 8-len(hex)) + hex
}

// PyStrRepr is repr() of a str; ok is false for a code point whose
// printability the Unicode tables of the two runtimes may not agree on.
func PyStrRepr(s string) (string, bool) {
	quote := '\''
	if strings.ContainsRune(s, '\'') && !strings.ContainsRune(s, '"') {
		quote = '"'
	}
	var out strings.Builder
	out.WriteRune(quote)
	for _, r := range s {
		switch {
		case r == quote || r == '\\':
			out.WriteRune('\\')
			out.WriteRune(r)
		case r == '\t':
			out.WriteString(`\t`)
		case r == '\n':
			out.WriteString(`\n`)
		case r == '\r':
			out.WriteString(`\r`)
		case r >= 0x20 && r < 0x7f, r >= 0x80 && unicode.IsPrint(r):
			out.WriteRune(r)
		case r < 0x20 || r == 0x7f, unicode.In(r, unicode.Cc, unicode.Cf, unicode.Zs, unicode.Zl, unicode.Zp, unicode.Co):
			out.WriteString(pyEscapeRune(r))
		default:
			return "", false
		}
	}
	out.WriteRune(quote)
	return out.String(), true
}
