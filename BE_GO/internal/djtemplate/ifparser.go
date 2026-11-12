package djtemplate

import (
	"math"
	"math/big"
)

// A port of django.template.smartif (the {% if %} expression parser): a
// Pratt parser with Django's binding powers. Operators evaluate like
// Python; an exception inside an operator makes it False, as in Django
// (errorTypeMismatch below); refusals of this port still propagate.

type ifExpr interface {
	eval(r *renderer) (any, error)
}

type ifLiteral struct{ expr *filterExpr }

type ifPrefix struct {
	op      string
	operand ifExpr
}

type ifInfix struct {
	op          string
	left, right ifExpr
}

var bindingPower = map[string]int{
	"or": 6, "and": 7, "not": 8, "in": 9, "not in": 9,
	"is": 10, "is not": 10, "==": 10, "!=": 10, ">": 10, ">=": 10, "<": 10, "<=": 10,
}

type ifToken struct {
	op      string // "" for a literal
	literal *filterExpr
}

type ifParser struct {
	tokens []ifToken
	pos    int
}

func parseIf(bits []string) (ifExpr, error) {
	var tokens []ifToken
	for i := 0; i < len(bits); i++ {
		bit := bits[i]
		switch {
		case bit == "is" && i+1 < len(bits) && bits[i+1] == "not":
			tokens = append(tokens, ifToken{op: "is not"})
			i++
		case bit == "not" && i+1 < len(bits) && bits[i+1] == "in":
			tokens = append(tokens, ifToken{op: "not in"})
			i++
		default:
			if _, ok := bindingPower[bit]; ok {
				tokens = append(tokens, ifToken{op: bit})
				continue
			}
			expr, err := parseFilterExpr(bit)
			if err != nil {
				return nil, err
			}
			tokens = append(tokens, ifToken{literal: expr})
		}
	}
	p := &ifParser{tokens: tokens}
	result, err := p.expression(0)
	if err != nil {
		return nil, err
	}
	if p.pos != len(p.tokens) {
		return nil, unsupported("unused tokens in if")
	}
	return result, nil
}

func (p *ifParser) next() (ifToken, bool) {
	if p.pos >= len(p.tokens) {
		return ifToken{}, false
	}
	token := p.tokens[p.pos]
	p.pos++
	return token, true
}

func (p *ifParser) lbp() int {
	if p.pos >= len(p.tokens) {
		return 0
	}
	token := p.tokens[p.pos]
	if token.op == "" {
		return 0
	}
	return bindingPower[token.op]
}

func (p *ifParser) expression(rbp int) (ifExpr, error) {
	token, ok := p.next()
	if !ok {
		return nil, unsupported("unexpected end of if expression")
	}
	var left ifExpr
	switch {
	case token.op == "":
		left = ifLiteral{expr: token.literal}
	case token.op == "not":
		operand, err := p.expression(bindingPower["not"])
		if err != nil {
			return nil, err
		}
		left = ifPrefix{op: "not", operand: operand}
	default:
		return nil, unsupported("not expecting %q in this position", token.op)
	}
	for rbp < p.lbp() {
		token, _ := p.next()
		if token.op == "not" {
			return nil, unsupported("not used as infix")
		}
		right, err := p.expression(bindingPower[token.op])
		if err != nil {
			return nil, err
		}
		left = ifInfix{op: token.op, left: left, right: right}
	}
	return left, nil
}

// errorTypeMismatch marks a Python exception (TypeError) inside an operator.
type errorTypeMismatch struct{}

func (errorTypeMismatch) Error() string { return "python operator exception" }

func (e ifLiteral) eval(r *renderer) (any, error) {
	value, err := r.resolveExpr(e.expr, true)
	if err != nil {
		return nil, err
	}
	if value == Missing {
		return nil, nil
	}
	return value, nil
}

func (e ifPrefix) eval(r *renderer) (any, error) {
	value, err := e.operand.eval(r)
	if err != nil {
		return nil, err
	}
	truth, err := Truthy(value)
	if err != nil {
		return nil, err
	}
	return !truth, nil
}

func (e ifInfix) eval(r *renderer) (any, error) {
	result, err := e.apply(r)
	if _, mismatch := err.(errorTypeMismatch); mismatch {
		return false, nil
	}
	return result, err
}

func (e ifInfix) apply(r *renderer) (any, error) {
	left, err := e.left.eval(r)
	if err != nil {
		return nil, err
	}
	switch e.op {
	case "or", "and":
		truth, err := Truthy(left)
		if err != nil {
			return nil, err
		}
		if (e.op == "or") == truth {
			return left, nil
		}
		return e.right.eval(r)
	}
	right, err := e.right.eval(r)
	if err != nil {
		return nil, err
	}
	switch e.op {
	case "==":
		return pythonEqual(left, right)
	case "!=":
		equal, err := pythonEqual(left, right)
		return !equal, err
	case "<", ">", "<=", ">=":
		return pythonCompare(e.op, left, right)
	case "in":
		return pythonIn(left, right)
	case "not in":
		in, err := pythonIn(left, right)
		return !in, err
	case "is":
		return pythonIs(left, right)
	case "is not":
		is, err := pythonIs(left, right)
		return !is, err
	}
	return nil, unsupported("operator %q", e.op)
}

func isText(value any) (string, bool) {
	switch v := value.(type) {
	case string:
		return v, true
	case Safe:
		return string(v), true
	}
	return "", false
}

// pythonEqual is x == y for the supported types: numbers exactly (bool is a
// number), strings by text, lists/tuples/dicts structurally, and a value of
// another type never equals one of these.
func pythonEqual(left, right any) (bool, error) {
	if left == nil || right == nil {
		return left == nil && right == nil, nil
	}
	if a, ok := isText(left); ok {
		b, ok := isText(right)
		return ok && a == b, nil
	}
	if _, ok := isText(right); ok {
		return false, nil
	}
	if a, ok := nonFiniteFloat(left); ok {
		b, ok := nonFiniteFloat(right)
		return ok && a == b, nil
	}
	if _, ok := nonFiniteFloat(right); ok {
		return false, nil
	}
	a, leftNumber, err := numeric(left)
	if err != nil {
		return false, err
	}
	b, rightNumber, err := numeric(right)
	if err != nil {
		return false, err
	}
	if leftNumber && rightNumber {
		return a.Cmp(b) == 0, nil
	}
	if leftNumber || rightNumber {
		other := right
		if rightNumber {
			other = left
		}
		if _, ok := other.(*Object); ok {
			return false, unsupported("== of a number and an object")
		}
		return false, nil
	}
	switch a := left.(type) {
	case List:
		b, ok := right.(List)
		if !ok {
			return false, nil
		}
		if len(a) != len(b) {
			return false, nil
		}
		for i := range a {
			equal, err := pythonEqual(a[i], b[i])
			if err != nil {
				return false, err
			}
			if !equal {
				return false, nil
			}
		}
		return true, nil
	case Tuple:
		b, ok := right.(Tuple)
		if !ok {
			return false, nil
		}
		return pythonEqual(List(a), List(b))
	case *Dict:
		b, ok := right.(*Dict)
		if !ok {
			return false, nil
		}
		if a.Len() != b.Len() {
			return false, nil
		}
		for _, key := range a.Keys() {
			leftItem, _ := a.Get(key)
			rightItem, present := b.Get(key)
			if !present {
				return false, nil
			}
			equal, err := pythonEqual(leftItem, rightItem)
			if err != nil {
				return false, err
			}
			if !equal {
				return false, nil
			}
		}
		return true, nil
	}
	return false, unsupported("== of %T and %T", left, right)
}

// nonFiniteFloat is a float Django's number comparisons accept as NaN or ±Inf.
func nonFiniteFloat(value any) (float64, bool) {
	if number, ok := value.(float64); ok && (math.IsInf(number, 0) || math.IsNaN(number)) {
		return number, true
	}
	return 0, false
}

// pythonCompare is x < y (and the others): numbers with numbers, str with
// str; any other pair is a TypeError.
func pythonCompare(op string, left, right any) (bool, error) {
	var cmp int
	if a, ok := isText(left); ok {
		b, ok := isText(right)
		if !ok {
			return false, errorTypeMismatch{}
		}
		cmp = compareCodePoints(a, b)
	} else {
		a, leftNumber, err := numeric(left)
		if err != nil {
			return false, err
		}
		b, rightNumber, err := numeric(right)
		if err != nil {
			return false, err
		}
		if !leftNumber || !rightNumber {
			if _, ok := left.(Date); ok {
				return false, unsupported("date comparison")
			}
			return false, errorTypeMismatch{}
		}
		cmp = a.Cmp(b)
	}
	switch op {
	case "<":
		return cmp < 0, nil
	case ">":
		return cmp > 0, nil
	case "<=":
		return cmp <= 0, nil
	}
	return cmp >= 0, nil
}

func compareCodePoints(a, b string) int {
	ra, rb := []rune(a), []rune(b)
	for i := 0; i < len(ra) && i < len(rb); i++ {
		if ra[i] != rb[i] {
			if ra[i] < rb[i] {
				return -1
			}
			return 1
		}
	}
	switch {
	case len(ra) < len(rb):
		return -1
	case len(ra) > len(rb):
		return 1
	}
	return 0
}

// pythonIn is x in y.
func pythonIn(left, right any) (bool, error) {
	switch container := right.(type) {
	case nil, bool, int64, *big.Int, float64, Decimal, Date, DateTime:
		return false, errorTypeMismatch{}
	case string, Safe:
		text, _ := isText(container)
		needle, ok := isText(left)
		if !ok {
			return false, errorTypeMismatch{}
		}
		return stringsContains(text, needle), nil
	case List:
		return listContains(container, left)
	case Tuple:
		return listContains(List(container), left)
	case *Dict:
		switch left.(type) {
		case *Dict, List:
			return false, errorTypeMismatch{} // unhashable
		}
		key, ok := isText(left)
		if !ok {
			return false, nil
		}
		_, found := container.Get(key)
		return found, nil
	}
	return false, unsupported("in of %T", right)
}

func stringsContains(text, needle string) bool {
	return len(needle) == 0 || indexOf(text, needle) >= 0
}

func indexOf(text, needle string) int {
	for i := 0; i+len(needle) <= len(text); i++ {
		if text[i:i+len(needle)] == needle {
			return i
		}
	}
	return -1
}

func listContains(list List, item any) (bool, error) {
	for _, element := range list {
		equal, err := pythonEqual(element, item)
		if err != nil {
			return false, err
		}
		if equal {
			return true, nil
		}
	}
	return false, nil
}

// pythonIs is identity: defined here for the singletons None, True and False.
func pythonIs(left, right any) (bool, error) {
	singleton := func(value any) bool {
		switch value.(type) {
		case nil, bool:
			return true
		}
		return false
	}
	if !singleton(left) && !singleton(right) {
		return false, unsupported("is between %T and %T", left, right)
	}
	if left == nil || right == nil {
		return left == nil && right == nil, nil
	}
	a, leftBool := left.(bool)
	b, rightBool := right.(bool)
	return leftBool && rightBool && a == b, nil
}
