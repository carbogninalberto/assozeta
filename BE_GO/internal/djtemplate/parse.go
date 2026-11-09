package djtemplate

import (
	"regexp"
	"strconv"
	"strings"
)

// Nodes.
type node interface{}

type textNode string

type varNode struct{ expr *filterExpr }

type ifNode struct {
	conditions []ifExpr // nil: else
	branches   [][]node
}

type forNode struct {
	loopvars []string
	sequence *filterExpr
	body     []node
}

// filterExpr is a FilterExpression: a variable or literal and its filters.
type filterExpr struct {
	value   variable
	filters []filterCall
}

type filterCall struct {
	name string
	arg  *variable
}

// variable is django.template.base.Variable: a literal (number or quoted
// string, marked safe) or a dotted lookup.
type variable struct {
	literal    any
	hasLiteral bool
	lookups    []string
}

type template struct {
	nodes  []node
	format bool // {% load format %}
}

// Django's tag_re: tags do not span lines.
var tokenPattern = regexp.MustCompile(`\{%.*?%\}|\{\{.*?\}\}|\{#.*?#\}`)

func parseTemplate(source string) (*template, error) {
	var tokens []string
	last := 0
	for _, span := range tokenPattern.FindAllStringIndex(source, -1) {
		if span[0] > last {
			tokens = append(tokens, source[last:span[0]])
		}
		tokens = append(tokens, source[span[0]:span[1]])
		last = span[1]
	}
	if last < len(source) {
		tokens = append(tokens, source[last:])
	}
	p := &parser{tokens: tokens}
	nodes, end, err := p.parse(nil)
	if err != nil {
		return nil, err
	}
	if end != "" {
		return nil, unsupported("unexpected {%% %s %%}", end)
	}
	return &template{nodes: nodes, format: p.format}, nil
}

type parser struct {
	tokens []string
	pos    int
	format bool
}

// parse reads nodes until one of the until tags; it returns that tag's contents.
func (p *parser) parse(until []string) ([]node, string, error) {
	var nodes []node
	for p.pos < len(p.tokens) {
		token := p.tokens[p.pos]
		p.pos++
		switch {
		case strings.HasPrefix(token, "{#") && strings.HasSuffix(token, "#}") && len(token) >= 4:
			continue
		case strings.HasPrefix(token, "{{") && strings.HasSuffix(token, "}}") && len(token) >= 4:
			content := strings.TrimSpace(token[2 : len(token)-2])
			if content == "" {
				return nil, "", unsupported("empty variable tag")
			}
			expr, err := parseFilterExpr(content)
			if err != nil {
				return nil, "", err
			}
			nodes = append(nodes, varNode{expr: expr})
		case strings.HasPrefix(token, "{%") && strings.HasSuffix(token, "%}") && len(token) >= 4:
			content := strings.TrimSpace(token[2 : len(token)-2])
			bits := smartSplit(content)
			if len(bits) == 0 {
				return nil, "", unsupported("empty block tag")
			}
			for _, stop := range until {
				if bits[0] == stop {
					return nodes, content, nil
				}
			}
			n, err := p.tag(bits)
			if err != nil {
				return nil, "", err
			}
			if n != nil {
				nodes = append(nodes, n)
			}
		default:
			nodes = append(nodes, textNode(token))
		}
	}
	if until != nil {
		return nil, "", unsupported("unclosed tag, expected %v", until)
	}
	return nodes, "", nil
}

func (p *parser) tag(bits []string) (node, error) {
	switch bits[0] {
	case "load":
		if len(bits) != 2 || bits[1] != "format" {
			return nil, unsupported("{%% load %s %%}", strings.Join(bits[1:], " "))
		}
		p.format = true
		return nil, nil
	case "if":
		result := &ifNode{}
		condition, err := parseIf(bits[1:])
		if err != nil {
			return nil, err
		}
		for {
			body, end, err := p.parse([]string{"elif", "else", "endif"})
			if err != nil {
				return nil, err
			}
			result.conditions = append(result.conditions, condition)
			result.branches = append(result.branches, body)
			endBits := smartSplit(end)
			switch endBits[0] {
			case "elif":
				if condition, err = parseIf(endBits[1:]); err != nil {
					return nil, err
				}
				continue
			case "else":
				if len(endBits) != 1 {
					return nil, unsupported("{%% %s %%}", end)
				}
				body, end, err := p.parse([]string{"endif"})
				if err != nil {
					return nil, err
				}
				if end != "endif" {
					return nil, unsupported("{%% %s %%}", end)
				}
				result.conditions = append(result.conditions, nil)
				result.branches = append(result.branches, body)
			default:
				if end != "endif" {
					return nil, unsupported("{%% %s %%}", end)
				}
			}
			return result, nil
		}
	case "for":
		// {% for x, y in sequence %}: no "reversed", no {% empty %}.
		if len(bits) < 4 || bits[len(bits)-2] != "in" {
			return nil, unsupported("{%% %s %%}", strings.Join(bits, " "))
		}
		names := strings.Join(bits[1:len(bits)-2], " ")
		var loopvars []string
		for _, name := range regexp.MustCompile(` *, *`).Split(names, -1) {
			if name == "" || strings.ContainsAny(name, " \t\n") || !identifierPattern.MatchString(name) {
				return nil, unsupported("for loop variable %q", name)
			}
			loopvars = append(loopvars, name)
		}
		sequence, err := parseFilterExpr(bits[len(bits)-1])
		if err != nil {
			return nil, err
		}
		body, end, err := p.parse([]string{"endfor", "empty"})
		if err != nil {
			return nil, err
		}
		if end != "endfor" {
			return nil, unsupported("{%% %s %%}", end)
		}
		return &forNode{loopvars: loopvars, sequence: sequence, body: body}, nil
	}
	return nil, unsupported("tag {%% %s %%}", bits[0])
}

var identifierPattern = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9_]*$`)

// smartSplit is django.utils.text.smart_split: whitespace-separated bits,
// keeping quoted strings (with their quotes) together.
func smartSplit(text string) []string {
	return smartSplitCompiled.FindAllString(text, -1)
}

var smartSplitCompiled = regexp.MustCompile(`(?:[^\s'"]*(?:(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')[^\s'"]*)+)|\S+`)

// Django's filter_re (without the i18n _() forms).
const (
	constantPattern = `"[^"\\]*(?:\\.[^"\\]*)*"|'[^'\\]*(?:\\.[^'\\]*)*'`
	numPattern      = `[-+.]?\d[\d.e]*`
)

var filterPattern = regexp.MustCompile(`^(?P<constant>` + constantPattern + `)|^(?P<var>[\w.]+|` + numPattern + `)|` +
	`(?:\s*\|\s*(?P<filter_name>\w+)(?::(?:(?P<constant_arg>` + constantPattern + `)|(?P<var_arg>[\w.]+|` + numPattern + `)))?)`)

func parseFilterExpr(token string) (*filterExpr, error) {
	expr := &filterExpr{}
	upto := 0
	seenVar := false
	names := filterPattern.SubexpNames()
	for _, match := range filterPattern.FindAllStringSubmatchIndex(token, -1) {
		if match[0] != upto {
			return nil, unsupported("could not parse %q", token)
		}
		groups := map[string]string{}
		for i, name := range names {
			if name != "" && match[2*i] >= 0 {
				groups[name] = token[match[2*i]:match[2*i+1]]
			}
		}
		switch {
		case !seenVar:
			text, ok := groups["constant"]
			if !ok {
				text, ok = groups["var"]
			}
			if !ok {
				return nil, unsupported("could not find variable in %q", token)
			}
			value, err := parseVariable(text)
			if err != nil {
				return nil, err
			}
			expr.value = value
			seenVar = true
		default:
			name, ok := groups["filter_name"]
			if !ok {
				return nil, unsupported("could not parse %q", token)
			}
			call := filterCall{name: name}
			arg, hasConstant := groups["constant_arg"]
			if !hasConstant {
				arg, hasConstant = groups["var_arg"]
			}
			if hasConstant {
				value, err := parseVariable(arg)
				if err != nil {
					return nil, err
				}
				call.arg = &value
			}
			expr.filters = append(expr.filters, call)
		}
		upto = match[1]
	}
	if upto != len(token) || !seenVar {
		return nil, unsupported("could not parse %q", token)
	}
	return expr, nil
}

// parseVariable is Variable.__init__: numbers, quoted strings (safe), lookups.
func parseVariable(text string) (variable, error) {
	if text == "" {
		return variable{}, unsupported("empty variable")
	}
	if strings.ContainsAny(text, ".") || strings.Contains(strings.ToLower(text), "e") {
		if number, ok := pythonFloatLiteral(text); ok {
			if strings.Contains(text, ".") && strings.HasSuffix(text, ".") {
				return variable{}, unsupported("variable %q", text)
			}
			return variable{literal: number, hasLiteral: true}, nil
		}
	} else if number, err := strconv.ParseInt(text, 10, 64); err == nil && regexp.MustCompile(`^[-+]?[0-9]+$`).MatchString(text) {
		return variable{literal: number, hasLiteral: true}, nil
	}
	if text[0] == '"' || text[0] == '\'' {
		quote := text[0]
		if len(text) < 2 || text[len(text)-1] != quote {
			return variable{}, unsupported("string literal %q", text)
		}
		inner := text[1 : len(text)-1]
		// unescape_string_literal: \<quote> and \\ only.
		inner = strings.NewReplacer(`\`+string(quote), string(quote), `\\`, `\`).Replace(inner)
		return variable{literal: Safe(inner), hasLiteral: true}, nil
	}
	if text[0] == '_' || strings.Contains(text, "._") {
		return variable{}, unsupported("variable %q", text)
	}
	if !regexp.MustCompile(`^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$`).MatchString(text) {
		return variable{}, unsupported("variable %q", text)
	}
	return variable{lookups: strings.Split(text, ".")}, nil
}

// pythonFloatLiteral is float(text) for the plain decimal forms.
func pythonFloatLiteral(text string) (float64, bool) {
	if !regexp.MustCompile(`^[-+]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?$`).MatchString(text) {
		return 0, false
	}
	value, err := strconv.ParseFloat(text, 64)
	return value, err == nil
}
