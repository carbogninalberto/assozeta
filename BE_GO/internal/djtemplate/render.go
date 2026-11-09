// Package djtemplate renders the document templates of BE/templates/document
// byte for byte as Django 5.2's render() does: a port of the template
// language restricted to what those templates use. {% load format %} (the
// application's format filter), {% if/elif/else/endif %} with Django's
// smartif operators, {% for a[, b] in seq %}, {# comments #}, variables with
// the filters safe, default, upper, lower, date, yesno, linebreaksbr, add,
// length, floatformat and format, autoescaping and the active language's
// number localization. Any other tag, filter, lookup or value type is
// refused with ErrUnsupported instead of being approximated.
//
// The template files under templates/ are copies of BE/templates; the golden
// test requires them to stay byte-identical and compares renders with
// Django's render_to_string output (testdata/django_renders.json).
package djtemplate

import (
	"embed"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"sync"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/emailrender"
)

//go:embed all:templates
var files embed.FS

// ErrUnsupported marks a template feature or context value outside the port.
var ErrUnsupported = errors.New("djtemplate: unsupported template input")

func unsupported(format string, args ...any) error {
	return fmt.Errorf("%w: %s", ErrUnsupported, fmt.Sprintf(format, args...))
}

// Templates of the document views.
const (
	Subscription        = "document/application/subscription.html"
	SubscriptionClassic = "document/application/subscription_classic.html"
	Invoice             = "document/application/invoice.html"
	InvoiceClassic      = "document/application/invoice_classic.html"
	Compensation        = "document/application/compensation.html"
	Model               = "document/application/model.html"
	Agonistica          = "document/medical-appointments/agonistica.html"
	NonAgonistica       = "document/medical-appointments/non-agonistica.html"
)

// Names lists every embedded template.
var Names = []string{Subscription, SubscriptionClassic, Invoice, InvoiceClassic, Compensation, Model, Agonistica, NonAgonistica}

var (
	loadOnce  sync.Once
	loaded    map[string]*template
	loadError error
)

func templates() (map[string]*template, error) {
	loadOnce.Do(func() {
		loaded = map[string]*template{}
		for _, name := range Names {
			raw, err := files.ReadFile("templates/" + name)
			if err != nil {
				loadError = err
				return
			}
			parsed, err := parseTemplate(string(raw))
			if err != nil {
				loadError = fmt.Errorf("%s: %w", name, err)
				return
			}
			loaded[name] = parsed
		}
	})
	return loaded, loadError
}

// Source returns an embedded template's bytes.
func Source(name string) ([]byte, error) { return files.ReadFile("templates/" + name) }

// Render is render(request, name, context) for an embedded template in the
// active language (LocaleMiddleware, or translation.activate in the view).
func Render(name string, context map[string]any, language string) (string, error) {
	all, err := templates()
	if err != nil {
		return "", err
	}
	parsed, ok := all[name]
	if !ok {
		return "", unsupported("template %q", name)
	}
	if !emailrender.KnownLanguage(language) {
		return "", unsupported("language %q", language)
	}
	root := map[string]any{"True": true, "False": false, "None": nil}
	r := &renderer{language: language, format: parsed.format, stack: []map[string]any{root, context}}
	var out strings.Builder
	if err := r.nodes(&out, parsed.nodes); err != nil {
		return "", err
	}
	return out.String(), nil
}

type renderer struct {
	language string
	format   bool
	stack    []map[string]any
}

func (r *renderer) nodes(out *strings.Builder, nodes []node) error {
	for _, n := range nodes {
		switch n := n.(type) {
		case textNode:
			out.WriteString(string(n))
		case varNode:
			value, err := r.resolveExpr(n.expr, false)
			if err != nil {
				return err
			}
			text, err := r.renderValue(value)
			if err != nil {
				return err
			}
			out.WriteString(text)
		case *ifNode:
			for i, condition := range n.conditions {
				matched := true
				if condition != nil {
					value, err := condition.eval(r)
					if err != nil {
						return err
					}
					if matched, err = Truthy(value); err != nil {
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
		case *forNode:
			if err := r.loop(out, n); err != nil {
				return err
			}
		default:
			return unsupported("node %T", n)
		}
	}
	return nil
}

func (r *renderer) loop(out *strings.Builder, n *forNode) error {
	sequence, err := r.resolveExpr(n.sequence, true)
	if err != nil {
		return err
	}
	var items []any
	switch v := sequence.(type) {
	case nil, missing:
	case List:
		items = v
	case Tuple:
		items = v
	case *Dict:
		for _, key := range v.Keys() {
			items = append(items, key)
		}
	default:
		return unsupported("for over %T", sequence)
	}
	for _, item := range items {
		scope := map[string]any{}
		if len(n.loopvars) > 1 {
			tuple, ok := item.(Tuple)
			if !ok {
				if list, isList := item.(List); isList {
					tuple = Tuple(list)
				} else {
					return unsupported("unpacking %T in for", item)
				}
			}
			if len(tuple) != len(n.loopvars) {
				return unsupported("need %d values to unpack in for loop; got %d", len(n.loopvars), len(tuple))
			}
			for i, name := range n.loopvars {
				scope[name] = tuple[i]
			}
		} else {
			scope[n.loopvars[0]] = item
		}
		r.stack = append(r.stack, scope)
		err := r.nodes(out, n.body)
		r.stack = r.stack[:len(r.stack)-1]
		if err != nil {
			return err
		}
	}
	return nil
}

// Names the request context processors add; the templates never read them.
var processorNames = map[string]bool{"request": true, "user": true, "perms": true, "messages": true, "DEFAULT_MESSAGE_LEVELS": true,
	"csrf_token": true, "debug": true, "sql_queries": true, "backends": true, "login_redirect": true}

// resolveExpr is FilterExpression.resolve: the variable (Missing when a
// lookup fails), then the filters. In a variable tag a failed lookup is
// string_if_invalid (""); in {% if %}/{% for %} (ignoreFailures) it is None.
func (r *renderer) resolveExpr(expr *filterExpr, ignoreFailures bool) (any, error) {
	value, err := r.resolveVariable(expr.value)
	if err != nil {
		return nil, err
	}
	if value == Missing {
		if ignoreFailures {
			value = nil
		} else {
			value = ""
		}
		if len(expr.filters) == 0 && ignoreFailures {
			return Missing, nil
		}
	}
	for _, call := range expr.filters {
		var arg any
		hasArg := call.arg != nil
		if hasArg {
			if !call.arg.hasLiteral {
				return nil, unsupported("variable filter argument")
			}
			arg = call.arg.literal
		}
		if value, err = r.filter(call.name, value, arg, hasArg); err != nil {
			return nil, err
		}
	}
	return value, nil
}

func (r *renderer) resolveVariable(v variable) (any, error) {
	if v.hasLiteral {
		return v.literal, nil
	}
	first := v.lookups[0]
	var current any = Missing
	for i := len(r.stack) - 1; i >= 0; i-- {
		if value, ok := r.stack[i][first]; ok {
			current = value
			break
		}
	}
	if current == Missing {
		if processorNames[first] {
			return nil, unsupported("context processor variable %q", first)
		}
		return Missing, nil
	}
	current, err := call(current)
	if err != nil {
		return nil, err
	}
	for _, bit := range v.lookups[1:] {
		next, err := lookup(current, bit)
		if err != nil {
			return nil, err
		}
		if next == Missing {
			return Missing, nil
		}
		if current, err = call(next); err != nil {
			return nil, err
		}
	}
	if err := checkValue(current); err != nil {
		return nil, err
	}
	return current, nil
}

func call(value any) (any, error) {
	if lazy, ok := value.(Lazy); ok {
		return lazy()
	}
	return value, nil
}

func checkValue(value any) error {
	switch value.(type) {
	case nil, string, Safe, bool, int64, float64, Decimal, Date, DateTime, *Dict, List, Tuple, *Object, missing:
		return nil
	}
	return unsupported("context value %T", value)
}

// Attribute names of Python's built-in types: a lookup that misses a dict
// key (or reads a str/list) and names one of them reaches the attribute.
var (
	dictAttributes = setOf("clear", "copy", "fromkeys", "get", "items", "keys", "pop", "popitem", "setdefault", "update", "values")
	strAttributes  = setOf("capitalize", "casefold", "center", "count", "encode", "endswith", "expandtabs", "find", "format",
		"format_map", "index", "isalnum", "isalpha", "isascii", "isdecimal", "isdigit", "isidentifier", "islower", "isnumeric",
		"isprintable", "isspace", "istitle", "isupper", "join", "ljust", "lower", "lstrip", "maketrans", "partition",
		"removeprefix", "removesuffix", "replace", "rfind", "rindex", "rjust", "rpartition", "rsplit", "rstrip", "split",
		"splitlines", "startswith", "strip", "swapcase", "title", "translate", "upper", "zfill")
	listAttributes = setOf("append", "clear", "copy", "count", "extend", "index", "insert", "pop", "remove", "reverse", "sort")
)

func setOf(names ...string) map[string]bool {
	result := map[string]bool{}
	for _, name := range names {
		result[name] = true
	}
	return result
}

func isDigits(text string) bool {
	if text == "" {
		return false
	}
	for _, r := range text {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

// lookup is one step of Variable._resolve_lookup: dictionary lookup, then
// attribute lookup, then list index; Missing when all fail.
func lookup(current any, bit string) (any, error) {
	switch v := current.(type) {
	case *Dict:
		if value, ok := v.Get(bit); ok {
			return value, nil
		}
		if bit == "items" {
			items := List{}
			for _, key := range v.Keys() {
				value, _ := v.Get(key)
				items = append(items, Tuple{key, value})
			}
			return items, nil
		}
		if dictAttributes[bit] {
			return nil, unsupported("dict attribute %q", bit)
		}
		return Missing, nil
	case *Object:
		value, ok := v.Attr(bit)
		if !ok && v.open {
			return Missing, nil
		}
		if !ok {
			return nil, unsupported("attribute %q of %s", bit, v.Class)
		}
		return value, nil
	case List, Tuple:
		list, _ := v.(List)
		if tuple, ok := v.(Tuple); ok {
			list = List(tuple)
		}
		if listAttributes[bit] {
			return nil, unsupported("list attribute %q", bit)
		}
		if isDigits(bit) {
			index, err := strconv.Atoi(bit)
			if err != nil || index >= len(list) {
				return Missing, nil
			}
			return list[index], nil
		}
		return Missing, nil
	case string, Safe:
		if strAttributes[bit] || isDigits(bit) {
			return nil, unsupported("str attribute %q", bit)
		}
		return Missing, nil
	case nil:
		return Missing, nil
	}
	return nil, unsupported("attribute %q of %T", bit, current)
}

// renderValue is render_value_in_context: localtime, localize, conditional_escape.
func (r *renderer) renderValue(value any) (string, error) {
	switch v := value.(type) {
	case nil:
		return "None", nil
	case Safe:
		return string(v), nil
	case string:
		return emailrender.Escape(v), nil
	case bool:
		if v {
			return "True", nil
		}
		return "False", nil
	case int64:
		return strconv.FormatInt(v, 10), nil
	case Decimal:
		if _, err := decimalRat(v); err != nil {
			return "", err
		}
		return emailrender.Localize(emailrender.Decimal(v), r.language)
	case float64:
		return emailrender.Localize(v, r.language)
	}
	return "", unsupported("rendering %T", value)
}
