// Package emailrender renders the transactional e-mail templates that Django
// views render in-request with render_to_string, byte for byte. It is a small
// port of the Django 5.2 template language restricted to what these templates
// use: {% extends %} with {% block %} overrides, {% load instance_branding %}
// with {% instance_email_brand as <var> %}, {% if/elif/else/endif %} with
// and/or/not over variables, {{ variable.path|filter:arg }} with the
// floatformat and linebreaksbr filters, Django's autoescaping and the active
// language's number localization (USE_THOUSAND_SEPARATOR is False, so only
// the decimal separator varies). Any other tag, filter, expression or context
// value type is refused with ErrUnsupported instead of being approximated.
//
// The template files under templates/ are copies of BE/templates; the golden
// test requires them to stay byte-identical and compares every render with
// Django's render_to_string output (testdata/django_renders.json).
package emailrender

import (
	"embed"
	"errors"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"sync"
)

//go:embed all:templates
var files embed.FS

// ErrUnsupported marks a template feature or context value outside the port.
var ErrUnsupported = errors.New("emailrender: unsupported template input")

func unsupported(format string, args ...any) error {
	return fmt.Errorf("%w: %s", ErrUnsupported, fmt.Sprintf(format, args...))
}

// Templates rendered by the ported views.
const (
	CollaboratorInvite  = "email/account/email_welcome_collaborator_message.html"
	PaymentPending      = "email/payment/pending.html"
	PaymentReceipt      = "email/payment/payment_receipt.html"
	PartialRegistration = "email/account/email_user_partial_registration.html"
	CertificateExpiring = "email/account/email_subscription_medical_certificate_expiring.html"
	CertificateExpired  = "email/account/email_subscription_medical_certificate_expired.html"
	ReminderEvent       = "email/reminders/event.html"
)

type node interface{}

type textNode string

type varNode struct{ expr filterExpr }

type ifNode struct {
	conditions []ifExpr // nil condition: else
	branches   [][]node
}

type blockNode struct {
	name  string
	nodes []node
}

// brandNode is {% instance_email_brand as <target> %}.
type brandNode struct{ target string }

type filterExpr struct {
	path    []string
	filters []filterCall
}

type filterCall struct {
	name string
	arg  any // nil, int64 or string
}

type template struct {
	parent string // {% extends %}; empty for a base template
	nodes  []node
	blocks map[string]*blockNode
}

// Django's tag_re: the tag contents do not span lines.
var tokenPattern = regexp.MustCompile(`\{%.*?%\}|\{\{.*?\}\}|\{#.*?#\}`)

var (
	pathPattern   = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$`)
	filterPattern = regexp.MustCompile(`^([a-z_]+)(?::("[^"]*"|-?[0-9]+))?$`)
)

type parser struct {
	tokens []string
	pos    int
	loaded bool
	blocks map[string]*blockNode
}

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
	p := &parser{tokens: tokens, blocks: map[string]*blockNode{}}
	nodes, end, err := p.parse(nil)
	if err != nil {
		return nil, err
	}
	if end != "" {
		return nil, unsupported("unexpected {%% %s %%}", end)
	}
	result := &template{nodes: nodes, blocks: p.blocks}
	// {% extends %} must be the first non-text node; the rest of a child
	// template outside its blocks is never rendered.
	for i, n := range nodes {
		if _, ok := n.(textNode); ok {
			continue
		}
		extends, ok := n.(extendsNode)
		if !ok {
			break
		}
		result.parent = string(extends)
		for _, rest := range nodes[i+1:] {
			switch rest.(type) {
			case textNode, *blockNode:
			default:
				return nil, unsupported("node outside blocks in a child template")
			}
		}
		break
	}
	for _, n := range nodes {
		if _, ok := n.(extendsNode); ok && result.parent == "" {
			return nil, unsupported("{%% extends %%} must be the first tag")
		}
	}
	return result, nil
}

type extendsNode string

// parse reads nodes until one of the end tags; it returns the tag contents
// that stopped it.
func (p *parser) parse(until []string) ([]node, string, error) {
	var nodes []node
	for p.pos < len(p.tokens) {
		token := p.tokens[p.pos]
		p.pos++
		switch {
		case strings.HasPrefix(token, "{{"):
			expr, err := parseFilterExpr(strings.TrimSpace(token[2 : len(token)-2]))
			if err != nil {
				return nil, "", err
			}
			nodes = append(nodes, varNode{expr})
		case strings.HasPrefix(token, "{#"):
			return nil, "", unsupported("template comments")
		case strings.HasPrefix(token, "{%"):
			contents := strings.TrimSpace(token[2 : len(token)-2])
			bits := strings.Fields(contents)
			if len(bits) == 0 {
				return nil, "", unsupported("empty tag")
			}
			for _, end := range until {
				if bits[0] == end {
					return nodes, contents, nil
				}
			}
			parsed, err := p.tag(bits)
			if err != nil {
				return nil, "", err
			}
			if parsed != nil {
				nodes = append(nodes, parsed)
			}
		default:
			nodes = append(nodes, textNode(token))
		}
	}
	if until != nil {
		return nil, "", unsupported("unclosed tag, expected one of %v", until)
	}
	return nodes, "", nil
}

func (p *parser) tag(bits []string) (node, error) {
	switch bits[0] {
	case "extends":
		if len(bits) != 2 || len(bits[1]) < 2 || bits[1][0] != '"' || bits[1][len(bits[1])-1] != '"' {
			return nil, unsupported("extends %v", bits)
		}
		return extendsNode(bits[1][1 : len(bits[1])-1]), nil
	case "load":
		if len(bits) != 2 || bits[1] != "instance_branding" {
			return nil, unsupported("load %v", bits)
		}
		p.loaded = true
		return nil, nil
	case "instance_email_brand":
		if !p.loaded || len(bits) != 3 || bits[1] != "as" || !pathPattern.MatchString(bits[2]) || strings.Contains(bits[2], ".") {
			return nil, unsupported("instance_email_brand %v", bits)
		}
		return brandNode{target: bits[2]}, nil
	case "block":
		if len(bits) != 2 {
			return nil, unsupported("block %v", bits)
		}
		if _, exists := p.blocks[bits[1]]; exists {
			return nil, unsupported("duplicate block %s", bits[1])
		}
		nodes, end, err := p.parse([]string{"endblock"})
		if err != nil {
			return nil, err
		}
		if fields := strings.Fields(end); len(fields) > 2 || (len(fields) == 2 && fields[1] != bits[1]) {
			return nil, unsupported("endblock %v", fields)
		}
		block := &blockNode{name: bits[1], nodes: nodes}
		p.blocks[bits[1]] = block
		return block, nil
	case "if":
		result := &ifNode{}
		condition, err := parseIfExpr(bits[1:])
		if err != nil {
			return nil, err
		}
		for {
			nodes, end, err := p.parse([]string{"elif", "else", "endif"})
			if err != nil {
				return nil, err
			}
			result.conditions = append(result.conditions, condition)
			result.branches = append(result.branches, nodes)
			fields := strings.Fields(end)
			switch fields[0] {
			case "endif":
				if len(fields) != 1 {
					return nil, unsupported("endif %v", fields)
				}
				return result, nil
			case "elif":
				if condition, err = parseIfExpr(fields[1:]); err != nil {
					return nil, err
				}
			case "else":
				if len(fields) != 1 {
					return nil, unsupported("else %v", fields)
				}
				nodes, end, err := p.parse([]string{"endif"})
				if err != nil {
					return nil, err
				}
				if end != "endif" {
					return nil, unsupported("endif %v", end)
				}
				result.conditions = append(result.conditions, nil)
				result.branches = append(result.branches, nodes)
				return result, nil
			}
		}
	}
	return nil, unsupported("tag %q", bits[0])
}

func parseFilterExpr(text string) (filterExpr, error) {
	parts := strings.Split(text, "|")
	if !pathPattern.MatchString(parts[0]) {
		return filterExpr{}, unsupported("variable %q", parts[0])
	}
	expr := filterExpr{path: strings.Split(parts[0], ".")}
	if expr.path[0] == "block" || expr.path[0] == "forloop" {
		return filterExpr{}, unsupported("variable %q", parts[0])
	}
	for _, part := range parts[1:] {
		match := filterPattern.FindStringSubmatch(part)
		if match == nil {
			return filterExpr{}, unsupported("filter %q", part)
		}
		call := filterCall{name: match[1]}
		switch {
		case match[2] == "":
		case match[2][0] == '"':
			call.arg = match[2][1 : len(match[2])-1]
		default:
			number, err := strconv.ParseInt(match[2], 10, 64)
			if err != nil {
				return filterExpr{}, unsupported("filter argument %q", match[2])
			}
			call.arg = number
		}
		switch call.name {
		case "floatformat":
		case "linebreaksbr":
			if call.arg != nil {
				return filterExpr{}, unsupported("linebreaksbr argument")
			}
		default:
			return filterExpr{}, unsupported("filter %q", call.name)
		}
		expr.filters = append(expr.filters, call)
	}
	return expr, nil
}

// ifExpr is Django's smartif restricted to variables with or, and, not
// (precedence: or < and < not).
type ifExpr interface{}

type ifOr struct{ left, right ifExpr }
type ifAnd struct{ left, right ifExpr }
type ifNot struct{ operand ifExpr }
type ifVar struct{ expr filterExpr }

func parseIfExpr(bits []string) (ifExpr, error) {
	if len(bits) == 0 {
		return nil, unsupported("empty if")
	}
	position := 0
	var orExpr, andExpr, unary func() (ifExpr, error)
	unary = func() (ifExpr, error) {
		if position >= len(bits) {
			return nil, unsupported("incomplete if")
		}
		bit := bits[position]
		position++
		if bit == "not" {
			operand, err := unary()
			return ifNot{operand}, err
		}
		if bit == "or" || bit == "and" || bit == "in" || bit == "is" || strings.ContainsAny(bit, "=<>!()\"'") {
			return nil, unsupported("if operator %q", bit)
		}
		expr, err := parseFilterExpr(bit)
		if err != nil {
			return nil, err
		}
		return ifVar{expr}, nil
	}
	andExpr = func() (ifExpr, error) {
		left, err := unary()
		for err == nil && position < len(bits) && bits[position] == "and" {
			position++
			var right ifExpr
			right, err = unary()
			left = ifAnd{left, right}
		}
		return left, err
	}
	orExpr = func() (ifExpr, error) {
		left, err := andExpr()
		for err == nil && position < len(bits) && bits[position] == "or" {
			position++
			var right ifExpr
			right, err = andExpr()
			left = ifOr{left, right}
		}
		return left, err
	}
	expr, err := orExpr()
	if err == nil && position != len(bits) {
		return nil, unsupported("if expression %v", bits)
	}
	return expr, err
}

var (
	loadOnce  sync.Once
	loaded    map[string]*template
	loadError error
)

// Names lists the embedded templates.
var names = []string{"email/_base.html", CollaboratorInvite, PaymentPending, PaymentReceipt,
	PartialRegistration, CertificateExpiring, CertificateExpired, ReminderEvent}

func templates() (map[string]*template, error) {
	loadOnce.Do(func() {
		loaded = map[string]*template{}
		for _, name := range names {
			source, err := files.ReadFile("templates/" + name)
			if err != nil {
				loadError = err
				return
			}
			parsed, err := parseTemplate(string(source))
			if err != nil {
				loadError = fmt.Errorf("%s: %w", name, err)
				return
			}
			loaded[name] = parsed
		}
	})
	return loaded, loadError
}

// Options carry what Django reads outside the context: the active language
// (LocaleMiddleware) and the instance_email_brand tag's value (see Brand).
type Options struct {
	Language string
	Brand    map[string]any
}

// Render is render_to_string(name, context) for an embedded template.
// Context values may be nil (None), string, Safe, bool, int64, Decimal,
// float64 and map[string]any (dicts and model instances read by attribute).
func Render(name string, context map[string]any, options Options) (string, error) {
	all, err := templates()
	if err != nil {
		return "", err
	}
	child, ok := all[name]
	if !ok {
		return "", unsupported("template %q", name)
	}
	separator, ok := decimalSeparators[options.Language]
	if !ok {
		return "", unsupported("language %q", options.Language)
	}
	r := &renderer{separator: separator, brand: options.Brand, context: map[string]any{}}
	for key, value := range context {
		r.context[key] = value
	}
	root, overrides := child, map[string]*blockNode{}
	for depth := 0; root.parent != ""; depth++ {
		if depth > 4 {
			return "", unsupported("extends depth")
		}
		for key, block := range root.blocks {
			if _, exists := overrides[key]; !exists {
				overrides[key] = block
			}
		}
		parent, ok := all[root.parent]
		if !ok {
			return "", unsupported("template %q", root.parent)
		}
		root = parent
	}
	r.overrides = overrides
	var out strings.Builder
	if err := r.nodes(&out, root.nodes); err != nil {
		return "", err
	}
	return out.String(), nil
}

type renderer struct {
	separator string
	brand     map[string]any
	context   map[string]any
	overrides map[string]*blockNode
}

func (r *renderer) nodes(out *strings.Builder, nodes []node) error {
	for _, n := range nodes {
		switch n := n.(type) {
		case textNode:
			out.WriteString(string(n))
		case extendsNode:
			return unsupported("nested extends")
		case brandNode:
			if r.brand == nil {
				return unsupported("instance_email_brand without a brand")
			}
			r.context[n.target] = r.brand
		case *blockNode:
			block := n
			if override, ok := r.overrides[n.name]; ok {
				block = override
			}
			if err := r.nodes(out, block.nodes); err != nil {
				return err
			}
		case varNode:
			text, err := r.variable(n.expr)
			if err != nil {
				return err
			}
			out.WriteString(text)
		case *ifNode:
			for i, condition := range n.conditions {
				matched := true
				if condition != nil {
					var err error
					if matched, err = r.condition(condition); err != nil {
						return err
					}
				}
				if matched {
					if err := r.nodes(out, n.branches[i]); err != nil {
						return err
					}
					break
				}
			}
		default:
			return unsupported("node %T", n)
		}
	}
	return nil
}

// invalid is a variable Django cannot resolve (string_if_invalid is ”).
type invalid struct{}

func (r *renderer) resolve(path []string) (any, error) {
	var current any = r.context
	for _, bit := range path {
		switch value := current.(type) {
		case map[string]any:
			next, ok := value[bit]
			if !ok {
				return invalid{}, nil
			}
			current = next
		case nil:
			// getattr(None, bit) fails: VariableDoesNotExist.
			return invalid{}, nil
		default:
			return nil, unsupported("attribute %q of %T", bit, current)
		}
	}
	if err := checkValue(current); err != nil {
		return nil, err
	}
	return current, nil
}

func checkValue(value any) error {
	switch v := value.(type) {
	case nil, string, Safe, bool, int64, float64, invalid:
		return nil
	case Decimal:
		_, err := parseDecimal(string(v))
		return err
	case map[string]any:
		for _, item := range v {
			if err := checkValue(item); err != nil {
				return err
			}
		}
		return nil
	}
	return unsupported("context value %T", value)
}

func (r *renderer) condition(expr ifExpr) (bool, error) {
	switch e := expr.(type) {
	case ifOr:
		left, err := r.condition(e.left)
		if err != nil || left {
			return left, err
		}
		return r.condition(e.right)
	case ifAnd:
		left, err := r.condition(e.left)
		if err != nil || !left {
			return left, err
		}
		return r.condition(e.right)
	case ifNot:
		value, err := r.condition(e.operand)
		return !value, err
	case ifVar:
		if len(e.expr.filters) > 0 {
			return false, unsupported("filters in if")
		}
		value, err := r.resolve(e.expr.path)
		if err != nil {
			return false, err
		}
		return truthy(value)
	}
	return false, unsupported("if node %T", expr)
}

func truthy(value any) (bool, error) {
	switch v := value.(type) {
	case nil, invalid:
		return false, nil
	case bool:
		return v, nil
	case string:
		return v != "", nil
	case Safe:
		return v != "", nil
	case int64:
		return v != 0, nil
	case float64:
		return v != 0, nil
	case Decimal:
		parsed, err := parseDecimal(string(v))
		return err == nil && parsed.coefficient.Sign() != 0, err
	case map[string]any:
		return len(v) > 0, nil
	}
	return false, unsupported("truth value of %T", value)
}

func (r *renderer) variable(expr filterExpr) (string, error) {
	value, err := r.resolve(expr.path)
	if err != nil {
		return "", err
	}
	if _, missing := value.(invalid); missing {
		value = ""
	}
	for _, call := range expr.filters {
		switch call.name {
		case "floatformat":
			if value, err = floatformat(value, call.arg, r.separator); err != nil {
				return "", err
			}
		case "linebreaksbr":
			if value, err = linebreaksbr(value); err != nil {
				return "", err
			}
		}
	}
	return r.renderValue(value)
}

// renderValue is render_value_in_context: localize, then conditional_escape.
func (r *renderer) renderValue(value any) (string, error) {
	switch v := value.(type) {
	case nil:
		return "None", nil
	case Safe:
		return string(v), nil
	case string:
		return Escape(v), nil
	case bool:
		if v {
			return "True", nil
		}
		return "False", nil
	case int64:
		return strconv.FormatInt(v, 10), nil
	case Decimal:
		return localizeDecimal(string(v), r.separator)
	case float64:
		return localizeFloat(v, r.separator)
	}
	return "", unsupported("rendering %T", value)
}

// Safe is a string marked safe (mark_safe): rendered without escaping.
type Safe string

// Decimal is a decimal.Decimal given by its str() (for example a numeric
// column's text, "10.50").
type Decimal string

var escaper = strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;", "'", "&#x27;")

// Escape is django.utils.html.escape (html.escape with quote=True).
func Escape(text string) string { return escaper.Replace(text) }

// linebreaksbr: @stringfilter str(value), normalize_newlines, escape (unless
// safe) and "\n" -> "<br>"; the result is safe.
func linebreaksbr(value any) (any, error) {
	var text string
	escape := true
	switch v := value.(type) {
	case nil:
		text = "None"
	case string:
		text = v
	case Safe:
		text, escape = string(v), false
	case int64:
		text = strconv.FormatInt(v, 10)
	default:
		return nil, unsupported("linebreaksbr of %T", value)
	}
	text = strings.ReplaceAll(strings.ReplaceAll(text, "\r\n", "\n"), "\r", "\n")
	if escape {
		text = Escape(text)
	}
	return Safe(strings.ReplaceAll(text, "\n", "<br>")), nil
}
