package pdfexport

import (
	"math"
	"math/big"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pyunicodedata"
)

// The expressions a colour attribute may hold. reportlab reads a string it
// does not know as a colour name or a css colour with ast.literal_eval and,
// when that refuses it, with rl_extended_literal_eval (reportlab/lib/
// rl_safe_eval.py): literals, the names of the colours and of None/True/False,
// a unary + or -, the addition or subtraction of a complex number, and calls
// of the colour functions. Everything else raises SyntaxError or ValueError,
// which toColor swallows. This file parses the expression and evaluates those
// two readers; a node neither accepts is a "soft" failure.

type pyNodeKind uint8

const (
	pnConst pyNodeKind = iota
	pnName
	pnTuple
	pnList
	pnSet
	pnDict
	pnUnary
	pnBinOp
	pnCall
	pnUnsupported
)

type pyNode struct {
	kind     pyNodeKind
	value    color     // pnConst
	name     string    // pnName, the operator of pnUnary/pnBinOp, the function of pnCall
	items    []*pyNode // tuple, list, set elements; dict keys and values in pairs
	keywords []pyKeyword
	operand  *pyNode // pnUnary
	right    *pyNode // pnBinOp
}

type pyKeyword struct {
	name  string // empty for a ** keyword
	value *pyNode
}

// evalResult is the outcome of one reader: a value, a SyntaxError/ValueError
// the caller swallows, or an exception that fails the paragraph.
type evalResult uint8

const (
	evalOK evalResult = iota
	evalSoft
	evalHard
)

// --- tokenizer ------------------------------------------------------------------------------------------------------

type pyTokenKind uint8

const (
	ptEOF pyTokenKind = iota
	ptName
	ptNumber
	ptString
	ptOp
)

type pyToken struct {
	kind  pyTokenKind
	text  string // a name or an operator
	value color  // a number or string literal
	bytes bool   // the string literal is bytes
	bad   bool   // a construct this port does not decode (an f-string)
	pos   int
}

type pyLexer struct {
	src    string
	pos    int
	tokens []pyToken
	depth  int // brackets still open
}

// isStringPrefix reports whether a run of letters is a string prefix.
func isStringPrefix(word string) bool {
	switch strings.ToLower(word) {
	case "r", "b", "u", "f", "br", "rb", "fr", "rf":
		return true
	}
	return false
}

// parsePyExpression is ast.parse(s, mode='eval') for the expressions this port
// reads; ok is false for a string Python refuses (a SyntaxError) or one with a
// construct this port does not parse.
func parsePyExpression(src string) (*pyNode, bool) {
	if leadingIndent(src) || strings.ContainsRune(src, 0) {
		return nil, false
	}
	lexer := &pyLexer{src: src}
	if !lexer.lex() {
		return nil, false
	}
	parser := &pyParser{tokens: lexer.tokens}
	node, ok := parser.expression()
	if !ok || node == nil {
		return nil, false
	}
	// eval() reads a tuple display as well: "1, 2" and "1,".
	var items []*pyNode
	if parser.peek().kind == ptOp && parser.peek().text == "," {
		items = []*pyNode{node}
		for parser.accept(",") {
			if parser.peek().kind == ptEOF {
				return &pyNode{kind: pnTuple, items: items}, true
			}
			item, ok := parser.expression()
			if !ok {
				return nil, false
			}
			items = append(items, item)
		}
		node = &pyNode{kind: pnTuple, items: items}
	}
	if parser.peek().kind != ptEOF {
		return nil, false
	}
	return node, true
}

// leadingIndent reports an expression ast.parse refuses as indented: spaces or
// tabs before the first code of a line (a comment-only line may be indented).
func leadingIndent(src string) bool {
	i := 0
	for i < len(src) {
		indented := false
		for i < len(src) {
			switch src[i] {
			case ' ', '\t':
				indented = true
				i++
				continue
			case '\f':
				i++
				continue
			}
			break
		}
		if i >= len(src) {
			return false
		}
		switch src[i] {
		case '#':
			for i < len(src) && src[i] != '\n' {
				i++
			}
			continue
		case '\n', '\r':
			i++
			continue
		}
		if indented {
			return true
		}
		r, _ := utf8.DecodeRuneInString(src[i:])
		return isPySpace(r)
	}
	return false
}

func (l *pyLexer) fail() bool { return false }

func (l *pyLexer) lex() bool {
	for l.pos < len(l.src) {
		r, size := utf8.DecodeRuneInString(l.src[l.pos:])
		switch {
		case r == ' ' || r == '\t' || r == '\f' || r == '\n' || r == '\r':
			l.pos += size
		case r == '#':
			for l.pos < len(l.src) && l.src[l.pos] != '\n' {
				l.pos++
			}
		case r == '\\' && l.pos+size < len(l.src) && l.src[l.pos+size] == '\n':
			l.pos += size + 1
		case r == '(' || r == '[' || r == '{':
			l.depth++
			l.tokens = append(l.tokens, pyToken{kind: ptOp, text: string(r), pos: l.pos})
			l.pos += size
		case r == ')' || r == ']' || r == '}':
			if l.depth > 0 {
				l.depth--
			}
			l.tokens = append(l.tokens, pyToken{kind: ptOp, text: string(r), pos: l.pos})
			l.pos += size
		case r == '\'' || r == '"':
			if !l.string("") {
				return false
			}
		case r >= '0' && r <= '9' || r == '.' && l.pos+1 < len(l.src) && l.src[l.pos+1] >= '0' && l.src[l.pos+1] <= '9':
			if !l.number() {
				return false
			}
		case isPyNameStart(r):
			start := l.pos
			l.pos += size
			for l.pos < len(l.src) {
				next, nextSize := utf8.DecodeRuneInString(l.src[l.pos:])
				if !isPyNamePart(next) {
					break
				}
				l.pos += nextSize
			}
			word := l.src[start:l.pos]
			if l.pos < len(l.src) && (l.src[l.pos] == '\'' || l.src[l.pos] == '"') && isStringPrefix(word) {
				if !l.string(strings.ToLower(word)) {
					return false
				}
				continue
			}
			l.tokens = append(l.tokens, pyToken{kind: ptName, text: word, pos: start})
		default:
			op, ok := l.operator()
			if !ok {
				return false
			}
			l.tokens = append(l.tokens, pyToken{kind: ptOp, text: op, pos: l.pos})
			l.pos += len(op)
		}
	}
	l.tokens = append(l.tokens, pyToken{kind: ptEOF, pos: len(l.src)})
	return true
}

var pyOperators = []string{"**", "//", "<<", ">>", "<=", ">=", "==", "!=", ":=", "->", "...",
	"+", "-", "*", "/", "%", "@", "&", "|", "^", "~", "<", ">", "=", ",", ":", ".", ";", "!", "$", "?", "`"}

func (l *pyLexer) operator() (string, bool) {
	for _, op := range pyOperators {
		if strings.HasPrefix(l.src[l.pos:], op) {
			return op, true
		}
	}
	return "", false
}

func isPyNameStart(r rune) bool {
	return r == '_' || unicode.IsLetter(r) || r >= 0x80 && unicode.Is(unicode.Nl, r)
}

func isPyNamePart(r rune) bool {
	return r == '_' || unicode.IsLetter(r) || unicode.IsDigit(r) || r >= 0x80 && unicode.Is(unicode.Mn, r)
}

// number reads a numeric literal: an integer in decimal, binary, octal or hex
// notation, a float, or an imaginary.
func (l *pyLexer) number() bool {
	start := l.pos
	for l.pos < len(l.src) {
		c := l.src[l.pos]
		if c >= '0' && c <= '9' || c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c == '_' || c == '.' {
			l.pos++
			continue
		}
		break
	}
	value, ok := pyNumberValue(l.src[start:l.pos])
	if !ok {
		return false
	}
	l.tokens = append(l.tokens, pyToken{kind: ptNumber, value: value, pos: start})
	return true
}

// pyNumberValue is the value of a Python numeric literal.
func pyNumberValue(text string) (color, bool) {
	s := text
	imag := false
	if len(s) > 0 && (s[len(s)-1] == 'j' || s[len(s)-1] == 'J') {
		imag = true
		s = s[:len(s)-1]
	}
	if !imag && !strings.ContainsAny(s, ".eE") {
		base := 10
		digits := s
		switch {
		case strings.HasPrefix(s, "0x") || strings.HasPrefix(s, "0X"):
			base, digits = 16, s[2:]
		case strings.HasPrefix(s, "0o") || strings.HasPrefix(s, "0O"):
			base, digits = 8, s[2:]
		case strings.HasPrefix(s, "0b") || strings.HasPrefix(s, "0B"):
			base, digits = 2, s[2:]
		}
		n, ok := pyIntLiteralValue(digits, base)
		if !ok {
			return color{}, false
		}
		return intColor(n), true
	}
	f, ok := pyFloatLiteral(s)
	if !ok {
		return color{}, false
	}
	if imag {
		return color{kind: colorComplex, im: f}, true
	}
	return floatColor(f), true
}

// pyIntLiteral is the value of the digits of an integer literal, with single
// underscores between digits; a decimal literal with a leading zero is
// malformed.
func pyIntLiteralValue(digits string, base int) (*big.Int, bool) {
	if digits == "" {
		return nil, false
	}
	valid := func(c byte) bool {
		switch {
		case c >= '0' && c <= '9':
			return c < byte('0'+base)
		case base == 16 && c >= 'a' && c <= 'f':
			return true
		case base == 16 && c >= 'A' && c <= 'F':
			return true
		}
		return false
	}
	var out strings.Builder
	for i := 0; i < len(digits); i++ {
		switch {
		case valid(digits[i]):
			out.WriteByte(digits[i])
		case digits[i] == '_' && i > 0 && i+1 < len(digits) && valid(digits[i-1]) && valid(digits[i+1]):
		default:
			return nil, false
		}
	}
	plain := out.String()
	if base == 10 && len(plain) > 1 && plain[0] == '0' {
		return nil, false
	}
	value := new(big.Int)
	if _, ok := value.SetString(plain, base); !ok {
		return nil, false
	}
	return value, true
}

// pyFloatLiteral is the value of the digits of a float literal: digits with
// single underscores, an optional fraction and exponent.
func pyFloatLiteral(s string) (float64, bool) {
	digits := func(text string) (string, bool) {
		var out strings.Builder
		for i := 0; i < len(text); i++ {
			c := text[i]
			switch {
			case c >= '0' && c <= '9':
				out.WriteByte(c)
			case c == '_' && i > 0 && i+1 < len(text) && text[i-1] >= '0' && text[i-1] <= '9' &&
				text[i+1] >= '0' && text[i+1] <= '9':
			default:
				return "", false
			}
		}
		return out.String(), true
	}
	mantissa, exponent, hasExponent := strings.Cut(s, "e")
	if !hasExponent {
		mantissa, exponent, hasExponent = strings.Cut(s, "E")
	}
	intPart, fracPart, hasFraction := strings.Cut(mantissa, ".")
	if intPart == "" && fracPart == "" {
		return 0, false
	}
	intDigits, ok := digits(intPart)
	if !ok {
		return 0, false
	}
	fracDigits := ""
	if hasFraction {
		fracDigits, ok = digits(fracPart)
		if !ok {
			return 0, false
		}
	}
	expDigits := ""
	if hasExponent {
		sign := ""
		if strings.HasPrefix(exponent, "+") || strings.HasPrefix(exponent, "-") {
			sign, exponent = exponent[:1], exponent[1:]
		}
		expDigits, ok = digits(exponent)
		if !ok || expDigits == "" {
			return 0, false
		}
		expDigits = "e" + sign + expDigits
	}
	if intDigits == "" {
		intDigits = "0"
	}
	if fracDigits == "" {
		fracDigits = "0"
	}
	value, err := strconv.ParseFloat(intDigits+"."+fracDigits+expDigits, 64)
	if err != nil {
		// A literal beyond the range of float64 is an infinity (1e400), as
		// float() reads it.
		if rangeError, ok := err.(*strconv.NumError); !ok || rangeError.Err != strconv.ErrRange {
			return 0, false
		}
	}
	return value, true
}

// string reads a string literal with its prefix (already read), its quotes and
// its escapes; l.pos is at the opening quote.
func (l *pyLexer) string(prefix string) bool {
	start := l.pos
	raw := strings.Contains(prefix, "r")
	isBytes := strings.Contains(prefix, "b")
	isFormat := strings.Contains(prefix, "f")
	quote := l.src[l.pos]
	l.pos++
	triple := false
	if l.pos+1 < len(l.src) && l.src[l.pos] == quote && l.src[l.pos+1] == quote {
		triple = true
		l.pos += 2
	}
	var out strings.Builder
	for {
		if l.pos >= len(l.src) {
			return l.fail()
		}
		c := l.src[l.pos]
		if c == quote {
			if !triple {
				l.pos++
				break
			}
			if l.pos+2 < len(l.src) && l.src[l.pos+1] == quote && l.src[l.pos+2] == quote {
				l.pos += 3
				break
			}
			out.WriteByte(c)
			l.pos++
			continue
		}
		if c == '\\' {
			if raw {
				out.WriteByte(c)
				l.pos++
				if l.pos < len(l.src) {
					out.WriteByte(l.src[l.pos])
					l.pos++
				}
				continue
			}
			ok, unsupported := l.escape(&out, isBytes)
			if unsupported {
				// A construct this port does not decode: the literal is
				// refused, which makes the expression a soft failure.
				l.tokens = append(l.tokens, pyToken{kind: ptString, bad: true, pos: start})
				for l.pos < len(l.src) {
					if l.src[l.pos] == quote {
						if !triple {
							l.pos++
							break
						}
						if l.pos+2 < len(l.src) && l.src[l.pos+1] == quote && l.src[l.pos+2] == quote {
							l.pos += 3
							break
						}
					}
					l.pos++
				}
				return true
			}
			if !ok {
				return l.fail()
			}
			continue
		}
		if c == '\n' && !triple {
			return l.fail()
		}
		out.WriteByte(c)
		l.pos++
	}
	text := out.String()
	if isFormat {
		l.tokens = append(l.tokens, pyToken{kind: ptString, bad: true, pos: start})
		return true
	}
	if isBytes {
		l.tokens = append(l.tokens, pyToken{kind: ptString, bytes: true, value: color{kind: colorBytes, text: text}, pos: start})
		return true
	}
	l.tokens = append(l.tokens, pyToken{kind: ptString, value: strColor(text), pos: start})
	return true
}

// escape is one backslash escape of a string literal; unsupported reports an
// escape this port does not decode.
func (l *pyLexer) escape(out *strings.Builder, bytesLiteral bool) (ok, unsupported bool) {
	l.pos++ // the backslash
	if l.pos >= len(l.src) {
		return false, false
	}
	c := l.src[l.pos]
	switch c {
	case '\n':
		l.pos++
		return true, false
	case '\\', '\'', '"':
		out.WriteByte(c)
		l.pos++
		return true, false
	case 'a':
		out.WriteByte(0x07)
	case 'b':
		out.WriteByte(0x08)
	case 'f':
		out.WriteByte(0x0c)
	case 'n':
		out.WriteByte('\n')
	case 'r':
		out.WriteByte('\r')
	case 't':
		out.WriteByte('\t')
	case 'v':
		out.WriteByte(0x0b)
	case '0', '1', '2', '3', '4', '5', '6', '7':
		value := 0
		count := 0
		for count < 3 && l.pos < len(l.src) {
			d := l.src[l.pos]
			if d < '0' || d > '7' {
				break
			}
			value = value*8 + int(d-'0')
			l.pos++
			count++
		}
		out.WriteByte(byte(value))
		return true, false
	case 'x':
		l.pos++
		value, ok := hexDigits(l.src, &l.pos, 2)
		if !ok {
			return false, false
		}
		out.WriteByte(byte(value))
		return true, false
	case 'u', 'U':
		if bytesLiteral {
			out.WriteByte('\\')
			out.WriteByte(c)
			l.pos++
			return true, false
		}
		count := 4
		if c == 'U' {
			count = 8
		}
		l.pos++
		value, ok := hexDigits(l.src, &l.pos, count)
		if !ok {
			return false, false
		}
		if value > 0x10ffff || value >= 0xd800 && value <= 0xdfff {
			return false, false
		}
		out.WriteRune(rune(value))
		return true, false
	case 'N':
		// \N{NAME}: unicodedata.lookup of a name or an alias (not a named
		// sequence), which must be one character; a missing name, a malformed
		// escape and a multi-character answer are a SyntaxError of the literal.
		// A bytes literal does not read it as an escape.
		if bytesLiteral {
			out.WriteByte('\\')
			out.WriteByte('N')
			l.pos++
			return true, false
		}
		l.pos++
		if l.pos >= len(l.src) || l.src[l.pos] != '{' {
			return false, false
		}
		end := strings.IndexByte(l.src[l.pos:], '}')
		if end < 0 {
			return false, false
		}
		name := l.src[l.pos+1 : l.pos+end]
		character, ok := pyunicodedata.Lookup(name)
		if !ok || len([]rune(character)) != 1 {
			return false, false
		}
		out.WriteString(character)
		l.pos += end + 1
		return true, false
	}
	if bytesLiteral {
		out.WriteByte('\\')
		out.WriteByte(c)
		l.pos++
		return true, false
	}
	out.WriteByte('\\')
	out.WriteByte(c)
	l.pos++
	return true, false
}

func hexDigits(src string, pos *int, count int) (int, bool) {
	if *pos+count > len(src) {
		return 0, false
	}
	value := 0
	for i := 0; i < count; i++ {
		c := src[*pos+i]
		digit := -1
		switch {
		case c >= '0' && c <= '9':
			digit = int(c - '0')
		case c >= 'a' && c <= 'f':
			digit = int(c-'a') + 10
		case c >= 'A' && c <= 'F':
			digit = int(c-'A') + 10
		}
		if digit < 0 {
			return 0, false
		}
		value = value*16 + digit
	}
	*pos += count
	return value, true
}

// --- parser ---------------------------------------------------------------------------------------------------------

type pyParser struct {
	tokens []pyToken
	pos    int
}

func (p *pyParser) peek() pyToken { return p.tokens[p.pos] }

func (p *pyParser) peekAt(offset int) pyToken {
	if p.pos+offset >= len(p.tokens) {
		return p.tokens[len(p.tokens)-1]
	}
	return p.tokens[p.pos+offset]
}

func (p *pyParser) next() pyToken {
	tok := p.tokens[p.pos]
	if p.pos < len(p.tokens)-1 {
		p.pos++
	}
	return tok
}

func (p *pyParser) accept(op string) bool {
	if p.peek().kind == ptOp && p.peek().text == op {
		p.pos++
		return true
	}
	return false
}

func (p *pyParser) acceptWord(word string) bool {
	if p.peek().kind == ptName && p.peek().text == word {
		p.pos++
		return true
	}
	return false
}

func (p *pyParser) unsupported() *pyNode { return &pyNode{kind: pnUnsupported} }

// expression is a conditional expression.
func (p *pyParser) expression() (*pyNode, bool) {
	left, ok := p.orTest()
	if !ok {
		return nil, false
	}
	if p.acceptWord("if") {
		if _, ok := p.orTest(); !ok {
			return nil, false
		}
		if !p.acceptWord("else") {
			return nil, false
		}
		if _, ok := p.expression(); !ok {
			return nil, false
		}
		return p.unsupported(), true
	}
	return left, true
}

func (p *pyParser) orTest() (*pyNode, bool) {
	left, ok := p.andTest()
	if !ok {
		return nil, false
	}
	for p.acceptWord("or") {
		if _, ok := p.andTest(); !ok {
			return nil, false
		}
		left = p.unsupported()
	}
	return left, true
}

func (p *pyParser) andTest() (*pyNode, bool) {
	left, ok := p.notTest()
	if !ok {
		return nil, false
	}
	for p.acceptWord("and") {
		if _, ok := p.notTest(); !ok {
			return nil, false
		}
		left = p.unsupported()
	}
	return left, true
}

func (p *pyParser) notTest() (*pyNode, bool) {
	if p.acceptWord("not") {
		if _, ok := p.notTest(); !ok {
			return nil, false
		}
		return p.unsupported(), true
	}
	return p.comparison()
}

var pyComparisonOps = map[string]bool{"<": true, ">": true, "==": true, ">=": true, "<=": true, "!=": true}

func (p *pyParser) comparison() (*pyNode, bool) {
	left, ok := p.binary(1)
	if !ok {
		return nil, false
	}
	for {
		switch {
		case p.peek().kind == ptOp && pyComparisonOps[p.peek().text]:
			p.next()
			if p.accept("=") {
				// nothing: "<>" is a Python 2 operator and a syntax error
			}
		case p.peek().kind == ptName && p.peek().text == "in":
			p.next()
		case p.peek().kind == ptName && p.peek().text == "is":
			p.next()
			p.acceptWord("not")
		case p.peek().kind == ptName && p.peek().text == "not" && p.peekAt(1).kind == ptName && p.peekAt(1).text == "in":
			p.next()
			p.next()
		default:
			return left, true
		}
		if _, ok := p.binary(1); !ok {
			return nil, false
		}
		left = p.unsupported()
	}
}

var pyBinaryPrecedence = map[string]int{
	"|": 1, "^": 2, "&": 3, "<<": 4, ">>": 4, "+": 5, "-": 5,
	"*": 6, "@": 6, "/": 6, "%": 6, "//": 6,
}

// binary reads a binary expression.
func (p *pyParser) binary(minPrec int) (*pyNode, bool) {
	left, ok := p.factor()
	if !ok {
		return nil, false
	}
	for {
		tok := p.peek()
		if tok.kind != ptOp {
			return left, true
		}
		prec, ok := pyBinaryPrecedence[tok.text]
		if !ok || prec < minPrec {
			return left, true
		}
		op := tok.text
		p.next()
		right, ok := p.binary(prec + 1)
		if !ok {
			return nil, false
		}
		if op == "+" || op == "-" {
			left = &pyNode{kind: pnBinOp, name: op, right: right}
		} else {
			left = p.unsupported()
		}
	}
}

func (p *pyParser) factor() (*pyNode, bool) {
	tok := p.peek()
	if tok.kind == ptOp && (tok.text == "+" || tok.text == "-" || tok.text == "~") {
		op := tok.text
		p.next()
		operand, ok := p.factor()
		if !ok {
			return nil, false
		}
		if op == "~" {
			return p.unsupported(), true
		}
		return &pyNode{kind: pnUnary, name: op, operand: operand}, true
	}
	if p.acceptWord("await") {
		if _, ok := p.primary(); !ok {
			return nil, false
		}
		return p.unsupported(), true
	}
	return p.power()
}

func (p *pyParser) power() (*pyNode, bool) {
	left, ok := p.primary()
	if !ok {
		return nil, false
	}
	if p.accept("**") {
		if _, ok := p.factor(); !ok {
			return nil, false
		}
		return p.unsupported(), true
	}
	return left, true
}

// primary is an atom with its trailers: a call, a subscription or an
// attribute access.
func (p *pyParser) primary() (*pyNode, bool) {
	atom, ok := p.atom()
	if !ok {
		return nil, false
	}
	for {
		switch {
		case p.peek().kind == ptOp && p.peek().text == "(":
			args, keywords, ok := p.arguments()
			if !ok {
				return nil, false
			}
			if atom.kind == pnName {
				atom = &pyNode{kind: pnCall, name: atom.name, items: args, keywords: keywords}
			} else {
				atom = p.unsupported()
			}
		case p.peek().kind == ptOp && p.peek().text == "[":
			p.next()
			if !p.subscriptList() {
				return nil, false
			}
			atom = p.unsupported()
		case p.peek().kind == ptOp && p.peek().text == ".":
			p.next()
			if p.peek().kind != ptName {
				return nil, false
			}
			p.next()
			atom = p.unsupported()
		default:
			return atom, true
		}
	}
}

// arguments reads the arguments of a call, up to and including the closing
// parenthesis.
func (p *pyParser) arguments() ([]*pyNode, []pyKeyword, bool) {
	p.next() // (
	var args []*pyNode
	var keywords []pyKeyword
	if p.accept(")") {
		return args, keywords, true
	}
	seenKeyword := false
	for {
		if p.accept("**") {
			if _, ok := p.expression(); !ok {
				return nil, nil, false
			}
			keywords = append(keywords, pyKeyword{})
			seenKeyword = true
		} else if p.accept("*") {
			if _, ok := p.expression(); !ok {
				return nil, nil, false
			}
			args = append(args, p.unsupported())
		} else if p.peek().kind == ptName && p.peekAt(1).kind == ptOp && p.peekAt(1).text == "=" {
			name := p.next().text
			p.next()
			value, ok := p.expression()
			if !ok {
				return nil, nil, false
			}
			keywords = append(keywords, pyKeyword{name: name, value: value})
			seenKeyword = true
		} else {
			value, ok := p.expression()
			if !ok {
				return nil, nil, false
			}
			if seenKeyword {
				// a positional argument after a keyword is a Python error
				return nil, nil, false
			}
			args = append(args, value)
		}
		if p.accept(",") {
			if p.accept(")") {
				return args, keywords, true
			}
			continue
		}
		if p.accept(")") {
			return args, keywords, true
		}
		return nil, nil, false
	}
}

// subscriptList reads the contents of a subscription; the value is unsupported
// but its shape has to be valid Python.
func (p *pyParser) subscriptList() bool {
	for {
		if p.accept("]") {
			return true
		}
		if p.accept(":") {
			for p.isExpressionStart() {
				if _, ok := p.expression(); !ok {
					return false
				}
			}
		} else if p.isExpressionStart() {
			if _, ok := p.expression(); !ok {
				return false
			}
			if p.accept(":") {
				for p.isExpressionStart() {
					if _, ok := p.expression(); !ok {
						return false
					}
				}
			}
		}
		if p.accept(",") {
			continue
		}
		return p.accept("]")
	}
}

func (p *pyParser) isExpressionStart() bool {
	tok := p.peek()
	switch tok.kind {
	case ptName, ptNumber, ptString:
		return true
	case ptOp:
		switch tok.text {
		case "+", "-", "~", "(", "[", "{", "*":
			return true
		}
	}
	return false
}

func (p *pyParser) atom() (*pyNode, bool) {
	tok := p.peek()
	switch tok.kind {
	case ptNumber:
		p.next()
		return &pyNode{kind: pnConst, value: tok.value}, true
	case ptString:
		first := p.next()
		if first.bad {
			for p.peek().kind == ptString {
				p.next()
			}
			return p.unsupported(), true
		}
		value := first.value
		for p.peek().kind == ptString {
			next := p.next()
			if next.bad || next.bytes != first.bytes {
				return p.unsupported(), true
			}
			value.text += next.value.text
		}
		return &pyNode{kind: pnConst, value: value}, true
	case ptName:
		switch tok.text {
		case "True":
			p.next()
			return &pyNode{kind: pnConst, value: boolColor(true)}, true
		case "False":
			p.next()
			return &pyNode{kind: pnConst, value: boolColor(false)}, true
		case "None":
			p.next()
			return &pyNode{kind: pnConst, value: color{kind: colorNone}}, true
		case "lambda":
			p.next()
			for !p.accept(":") {
				if p.peek().kind == ptEOF {
					return nil, false
				}
				p.next()
			}
			if _, ok := p.expression(); !ok {
				return nil, false
			}
			return p.unsupported(), true
		}
		p.next()
		return &pyNode{kind: pnName, name: tok.text}, true
	case ptOp:
		switch tok.text {
		case "(":
			p.next()
			if p.accept(")") {
				return &pyNode{kind: pnTuple}, true
			}
			if p.acceptWord("yield") {
				for !p.accept(")") {
					if p.peek().kind == ptEOF {
						return nil, false
					}
					p.next()
				}
				return p.unsupported(), true
			}
			first, ok := p.expression()
			if !ok {
				return nil, false
			}
			if p.accept(")") {
				return first, true
			}
			if !p.accept(",") {
				return nil, false
			}
			items := []*pyNode{first}
			for {
				if p.accept(")") {
					return &pyNode{kind: pnTuple, items: items}, true
				}
				item, ok := p.starredExpression()
				if !ok {
					return nil, false
				}
				items = append(items, item)
				if p.accept(",") {
					continue
				}
				if p.accept(")") {
					return &pyNode{kind: pnTuple, items: items}, true
				}
				return nil, false
			}
		case "[":
			p.next()
			if p.accept("]") {
				return &pyNode{kind: pnList}, true
			}
			var items []*pyNode
			for {
				item, ok := p.starredExpression()
				if !ok {
					return nil, false
				}
				items = append(items, item)
				if p.accept(",") {
					if p.accept("]") {
						return &pyNode{kind: pnList, items: items}, true
					}
					continue
				}
				if p.accept("]") {
					return &pyNode{kind: pnList, items: items}, true
				}
				return nil, false
			}
		case "{":
			return p.dictOrSet()
		case "...":
			p.next()
			return &pyNode{kind: pnConst, value: color{kind: colorEllipsis}}, true
		}
	}
	return nil, false
}

// starredExpression reads an element of a display, which may be starred.
func (p *pyParser) starredExpression() (*pyNode, bool) {
	if p.accept("*") {
		if _, ok := p.expression(); !ok {
			return nil, false
		}
		return p.unsupported(), true
	}
	return p.expression()
}

func (p *pyParser) dictOrSet() (*pyNode, bool) {
	p.next() // {
	if p.accept("}") {
		return &pyNode{kind: pnDict}, true
	}
	if p.accept("**") {
		if _, ok := p.expression(); !ok {
			return nil, false
		}
		return p.dictRest([]*pyNode{nil, nil})
	}
	first, ok := p.starredExpression()
	if !ok {
		return nil, false
	}
	if p.accept(":") {
		value, ok := p.expression()
		if !ok {
			return nil, false
		}
		return p.dictRest([]*pyNode{first, value})
	}
	items := []*pyNode{first}
	for {
		if p.accept(",") {
			if p.accept("}") {
				return &pyNode{kind: pnSet, items: items}, true
			}
			item, ok := p.starredExpression()
			if !ok {
				return nil, false
			}
			items = append(items, item)
			continue
		}
		if p.accept("}") {
			return &pyNode{kind: pnSet, items: items}, true
		}
		return nil, false
	}
}

func (p *pyParser) dictRest(items []*pyNode) (*pyNode, bool) {
	for {
		if p.accept("}") {
			return &pyNode{kind: pnDict, items: items}, true
		}
		if !p.accept(",") {
			return nil, false
		}
		if p.accept("}") {
			return &pyNode{kind: pnDict, items: items}, true
		}
		if p.accept("**") {
			if _, ok := p.expression(); !ok {
				return nil, false
			}
			items = append(items, nil, nil)
			continue
		}
		key, ok := p.expression()
		if !ok {
			return nil, false
		}
		if !p.accept(":") {
			return nil, false
		}
		value, ok := p.expression()
		if !ok {
			return nil, false
		}
		items = append(items, key, value)
	}
}

// --- literal_eval ---------------------------------------------------------------------------------------------------

// literalEval is ast.literal_eval of the node (CPython 3.13's): constants,
// tuples, lists, sets and dicts, a signed number and a number added to a
// complex one.
func literalEval(node *pyNode) (color, evalResult) {
	switch node.kind {
	case pnConst:
		return node.value, evalOK
	case pnTuple, pnList, pnSet:
		items := make([]color, len(node.items))
		for i, item := range node.items {
			value, res := literalEval(item)
			if res != evalOK {
				return color{}, res
			}
			items[i] = value
		}
		if node.kind == pnSet {
			for _, item := range items {
				if !hashableValue(item) {
					return color{}, evalHard
				}
			}
			return color{kind: colorOpaque, key: "set"}, evalOK
		}
		return seqColor(items, node.kind == pnList), evalOK
	case pnDict:
		out := color{kind: colorDict}
		for i := 0; i+1 < len(node.items); i += 2 {
			if node.items[i] == nil {
				return color{}, evalSoft // {**mapping}
			}
			key, res := literalEval(node.items[i])
			if res != evalOK {
				return color{}, res
			}
			if !hashableValue(key) {
				return color{}, evalHard
			}
			value, res := literalEval(node.items[i+1])
			if res != evalOK {
				return color{}, res
			}
			setDictEntry(&out, key, value)
		}
		return out, evalOK
	case pnUnary:
		if node.name != "+" && node.name != "-" {
			return color{}, evalSoft
		}
		operand := node.operand
		if operand.kind != pnConst || !isRealNumber(operand.value) {
			return color{}, evalSoft
		}
		return signedNumber(operand.value, node.name == "-"), evalOK
	case pnBinOp:
		if node.name != "+" && node.name != "-" {
			return color{}, evalSoft
		}
		left := nodeLeftSignedNumber(node)
		if left == nil || node.right == nil || node.right.kind != pnConst || node.right.value.kind != colorComplex {
			return color{}, evalSoft
		}
		re, im := colorComplexParts(*left)
		if node.name == "+" {
			return color{kind: colorComplex, f: re + node.right.value.f, im: im + node.right.value.im}, evalOK
		}
		return color{kind: colorComplex, f: re - node.right.value.f, im: im - node.right.value.im}, evalOK
	}
	return color{}, evalSoft
}

// isRealNumber is type(value) in (int, float, complex) of a literal: a bool is
// not one, as in literal_eval's _convert_num.
func isRealNumber(c color) bool {
	switch c.kind {
	case colorInt, colorFloat, colorComplex:
		return true
	}
	return false
}

// signedNumber is +operand or -operand of a numeric literal.
func signedNumber(value color, negative bool) color {
	switch value.kind {
	case colorInt:
		n := new(big.Int).Set(value.num)
		if negative {
			n.Neg(n)
		}
		return intColor(n)
	case colorFloat:
		if negative {
			return floatColor(-value.f)
		}
		return value
	case colorComplex:
		if negative {
			return color{kind: colorComplex, f: -value.f, im: -value.im}
		}
	}
	return value
}

// nodeLeftSignedNumber is _convert_signed_num of a binary expression's left
// side: a numeric constant with an optional sign.
func nodeLeftSignedNumber(node *pyNode) *color {
	left := node
	if left.kind == pnUnary && (left.name == "+" || left.name == "-") {
		if left.operand.kind != pnConst || !isRealNumber(left.operand.value) {
			return nil
		}
		value := signedNumber(left.operand.value, left.name == "-")
		return &value
	}
	if left.kind != pnConst || left.value.kind != colorInt && left.value.kind != colorFloat {
		return nil
	}
	value := left.value
	return &value
}

// hashableValue reports whether Python can use the value as a dict key or a
// set member.
func hashableValue(c color) bool {
	switch c.kind {
	case colorTuple:
		for _, item := range c.items {
			if !hashableValue(item) {
				return false
			}
		}
		return true
	case colorList, colorDict, colorOpaque:
		return false
	}
	return true
}

// setDictEntry is dict[key] = value: an equal key keeps its place and takes the
// new value.
func setDictEntry(out *color, key, value color) {
	for i := range out.pairs {
		if colorEq(out.pairs[i].key, key) {
			out.pairs[i].value = value
			return
		}
	}
	out.pairs = append(out.pairs, colorPair{key: key, value: value})
}

// --- extended literal eval ------------------------------------------------------------------------------------------

// extendedEval is rl_extended_literal_eval of the node: literal values, the
// names of the colours, a signed number, a complex sum and the calls of the
// colour functions.
func extendedEval(node *pyNode) (color, evalResult) {
	switch node.kind {
	case pnConst:
		if node.value.kind == colorEllipsis {
			return color{}, evalSoft // not one of the safe constants
		}
		return node.value, evalOK
	case pnTuple, pnList:
		items := make([]color, len(node.items))
		for i, item := range node.items {
			value, res := extendedEval(item)
			if res != evalOK {
				return color{}, res
			}
			items[i] = value
		}
		return seqColor(items, node.kind == pnList), evalOK
	case pnDict:
		out := color{kind: colorDict}
		for i := 0; i+1 < len(node.items); i += 2 {
			if node.items[i] == nil {
				return color{}, evalSoft
			}
			key, res := extendedEval(node.items[i])
			if res != evalOK {
				return color{}, res
			}
			if !hashableValue(key) {
				return color{}, evalHard
			}
			value, res := extendedEval(node.items[i+1])
			if res != evalOK {
				return color{}, res
			}
			setDictEntry(&out, key, value)
		}
		return out, evalOK
	case pnName:
		if value, ok := paraColorNames[node.name]; ok {
			return value, evalOK
		}
		return color{}, evalSoft
	case pnUnary:
		if node.name != "+" && node.name != "-" {
			return color{}, evalSoft
		}
		if !isLiteralOperand(node.operand) {
			return color{}, evalSoft
		}
		operand, res := extendedEval(node.operand)
		if res != evalOK {
			return color{}, res
		}
		if !isNumberColor(operand) {
			return color{}, evalHard // unary + of a str, a tuple...
		}
		return signedNumber(operand, node.name == "-"), evalOK
	case pnBinOp:
		if node.name != "+" && node.name != "-" {
			return color{}, evalSoft
		}
		if node.right == nil {
			return color{}, evalSoft
		}
		switch node.right.kind {
		case pnUnary, pnBinOp:
			// rl_extended_literal_eval reads node.right.n, which only a
			// Constant has: an AttributeError fails the paragraph.
			return color{}, evalHard
		case pnConst:
			if node.right.value.kind == colorComplex {
				// The left check reads the undefined name astBinOp: a
				// NameError fails the paragraph.
				return color{}, evalHard
			}
		}
		return color{}, evalSoft
	case pnCall:
		if node.name == "" || !paraIsCallable(node.name) {
			return color{}, evalSoft
		}
		args := make([]color, len(node.items))
		for i, item := range node.items {
			value, res := extendedEval(item)
			if res != evalOK {
				return color{}, res
			}
			args[i] = value
		}
		keywords, res := keywordValues(node)
		if res != evalOK {
			return color{}, res
		}
		return callParaCallable(node.name, args, keywords)
	}
	return color{}, evalSoft
}

// isLiteralOperand reports whether a unary operator's operand is a shape
// rl_extended_literal_eval converts: a number, a signed number or a sum.
func isLiteralOperand(node *pyNode) bool {
	if node == nil {
		return false
	}
	switch node.kind {
	case pnUnary, pnBinOp:
		return true
	case pnConst:
		return isRealNumber(node.value)
	}
	return false
}

// keywordValues evaluates the keyword arguments of a call and returns them by
// name; a ** keyword cannot name a parameter.
func keywordValues(node *pyNode) (map[string]color, evalResult) {
	out := map[string]color{}
	for _, keyword := range node.keywords {
		value, res := extendedEval(keyword.value)
		if res != evalOK {
			return nil, res
		}
		if keyword.name == "" {
			return nil, evalHard
		}
		if _, exists := out[keyword.name]; exists {
			return nil, evalHard
		}
		out[keyword.name] = value
	}
	return out, evalOK
}

// math helpers on Python values ---------------------------------------------------------------------------------------

func isIntOrFloat(c color) bool {
	return c.kind == colorInt || c.kind == colorBool || c.kind == colorFloat
}

// numberFloat is a real number as a float, for arithmetic Python does in
// floats.
func numberFloat(c color) (float64, bool) {
	switch c.kind {
	case colorInt:
		if c.num == nil {
			return 0, true
		}
		f, _ := new(big.Float).SetInt(c.num).Float64()
		return f, true
	case colorBool, colorFloat:
		return c.f, true
	}
	return 0, false
}

// intFromValue is int(x) of a real number value.
func intFromValue(c color) (*big.Int, bool) {
	switch c.kind {
	case colorInt:
		return new(big.Int).Set(c.num), true
	case colorBool:
		if c.f != 0 {
			return big.NewInt(1), true
		}
		return big.NewInt(0), true
	case colorFloat:
		if math.IsNaN(c.f) || math.IsInf(c.f, 0) {
			return nil, false
		}
		out, _ := new(big.Float).SetFloat64(c.f).Int(nil)
		return out, true
	}
	return nil, false
}
