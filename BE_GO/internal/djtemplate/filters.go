package djtemplate

import (
	"math"
	"math/big"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/emailrender"
)

// filter applies one Django filter. Results keep Python's safety: a filter
// marked is_safe keeps a SafeString input safe; others return plain str
// unless the returned object itself is a SafeString.
func (r *renderer) filter(name string, value, arg any, hasArg bool) (any, error) {
	if value == Missing {
		value = nil
	}
	switch name {
	case "safe":
		if hasArg {
			return nil, unsupported("safe with an argument")
		}
		text, err := PythonStr(value)
		return Safe(text), err
	case "upper", "lower":
		if hasArg {
			return nil, unsupported("%s with an argument", name)
		}
		text, err := PythonStr(value)
		if err != nil {
			return nil, err
		}
		if name == "upper" {
			return pythonUpper(text)
		}
		return pythonLower(text)
	case "default":
		if !hasArg {
			return nil, unsupported("default without an argument")
		}
		truth, err := Truthy(value)
		if err != nil {
			return nil, err
		}
		if truth {
			return value, nil
		}
		return arg, nil
	case "date":
		format, ok := arg.(Safe)
		if !hasArg || !ok {
			return nil, unsupported("date without a format")
		}
		return r.date(value, string(format))
	case "yesno":
		return yesno(value, arg, hasArg)
	case "linebreaksbr":
		if hasArg {
			return nil, unsupported("linebreaksbr with an argument")
		}
		text, err := PythonStr(value)
		if err != nil {
			return nil, err
		}
		_, safe := value.(Safe)
		text = strings.ReplaceAll(strings.ReplaceAll(text, "\r\n", "\n"), "\r", "\n")
		if !safe {
			text = emailrender.Escape(text)
		}
		return Safe(strings.ReplaceAll(text, "\n", "<br>")), nil
	case "add":
		return add(value, arg, hasArg)
	case "length":
		if hasArg {
			return nil, unsupported("length with an argument")
		}
		return length(value)
	case "floatformat":
		var precision any
		if hasArg {
			number, ok := arg.(int64)
			if !ok {
				return nil, unsupported("floatformat argument %v", arg)
			}
			precision = number
		}
		var input any
		switch v := value.(type) {
		case nil, string, int64, float64:
			input = v
		case Safe:
			input = string(v)
		case Decimal:
			input = emailrender.Decimal(v)
		default:
			return nil, unsupported("floatformat of %T", value)
		}
		result, err := emailrender.FloatFormat(input, precision, r.language)
		if err != nil {
			return nil, err
		}
		switch v := result.(type) {
		case emailrender.Safe:
			return Safe(v), nil
		case string:
			return Safe(v), nil
		}
		return nil, unsupported("floatformat result %T", result)
	case "format":
		if !r.format {
			return nil, unsupported("format filter without {%% load format %%}")
		}
		return formatFilter(value, arg, hasArg)
	}
	return nil, unsupported("filter %q", name)
}

// yesno: "yes,no[,maybe]" (maybe defaults to no); None is maybe.
func yesno(value, arg any, hasArg bool) (any, error) {
	text, ok := arg.(Safe)
	if !hasArg || !ok {
		return nil, unsupported("yesno without a string argument")
	}
	bits := strings.Split(string(text), ",")
	if len(bits) < 2 {
		return value, nil
	}
	if len(bits) > 3 {
		// yes, no, maybe = bits raises ValueError: the 2-value fallback.
		return nil, unsupported("yesno with more than three values")
	}
	yes, no, maybe := bits[0], bits[1], bits[1]
	if len(bits) == 3 {
		maybe = bits[2]
	}
	if value == nil {
		return maybe, nil
	}
	truth, err := Truthy(value)
	if err != nil {
		return nil, err
	}
	if truth {
		return yes, nil
	}
	return no, nil
}

// add: int(value) + int(arg), else value + arg, else "". Only a str
// argument that is not an integer is ported.
func add(value, arg any, hasArg bool) (any, error) {
	argText, ok := arg.(Safe)
	if !hasArg || !ok {
		return nil, unsupported("add without a string argument")
	}
	if _, err := strconv.Atoi(strings.TrimSpace(string(argText))); err == nil || strings.ContainsAny(string(argText), "0123456789") {
		return nil, unsupported("add with a numeric argument")
	}
	switch v := value.(type) {
	case Safe:
		return v + argText, nil
	case string:
		return v + string(argText), nil
	case nil, bool, int64, *big.Int, float64, Decimal, Date, DateTime, *Object, *Dict, List, Tuple:
		// int(arg) fails; value + str is a TypeError (list + str too).
		return "", nil
	}
	return nil, unsupported("add to %T", value)
}

// length: len(value), 0 when len() fails.
func length(value any) (any, error) {
	switch v := value.(type) {
	case string:
		return int64(utf8.RuneCountInString(v)), nil
	case Safe:
		return int64(utf8.RuneCountInString(string(v))), nil
	case List:
		return int64(len(v)), nil
	case Tuple:
		return int64(len(v)), nil
	case *Dict:
		return int64(v.Len()), nil
	case nil, bool, int64, *big.Int, float64, Decimal, Date, DateTime:
		return int64(0), nil
	}
	return nil, unsupported("length of %T", value)
}

// formatFilter is application.templatetags.format: fmt.format(value) with
// ',' and '.' swapped through '-'. Only "{:0.2f}"-style fixed formats of
// numbers are ported; anything else raises in Python (the 500 page) or is
// refused.
func formatFilter(value, arg any, hasArg bool) (any, error) {
	spec, ok := arg.(Safe)
	if !hasArg || !ok || string(spec) != "{:0.2f}" {
		return nil, unsupported("format %v", arg)
	}
	var text string
	switch v := value.(type) {
	case bool:
		text = "0.00"
		if v {
			text = "1.00"
		}
	case int64:
		text = strconv.FormatFloat(float64(v), 'f', 2, 64)
	case *big.Int:
		// int.__format__ with a float presentation converts to double first,
		// raising OverflowError beyond float64's range.
		number, _ := new(big.Float).SetInt(v).Float64()
		if math.IsInf(number, 0) {
			return nil, pythonError("format of an integer beyond float64")
		}
		text = strconv.FormatFloat(number, 'f', 2, 64)
	case float64:
		if math.IsInf(v, 0) || math.IsNaN(v) {
			repr, err := pythonFloatRepr(v)
			if err != nil {
				return nil, err
			}
			text = repr
			break
		}
		text = strconv.FormatFloat(v, 'f', 2, 64)
	case Decimal:
		r, err := decimalRat(v)
		if err != nil {
			return nil, err
		}
		text = decimalFixedHalfEven(r, 2, strings.HasPrefix(string(v), "-"))
	default:
		// str.format of a str/None/list/dict with ".2f" raises: Django
		// answers the 500 page.
		return nil, pythonError("format of %T", value)
	}
	// .replace(',', '-').replace('.', ',').replace('-', '.') maps ',' and '-'
	// to '.' and '.' to ',': the same as one simultaneous replacement.
	return strings.NewReplacer(",", ".", ".", ",", "-", ".").Replace(text), nil
}

// decimalFixedHalfEven is format(Decimal, '.2f') under the default context
// (ROUND_HALF_EVEN), keeping a negative sign on zero.
func decimalFixedHalfEven(value *big.Rat, places int, negative bool) string {
	scale := new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(places)), nil)
	scaled := new(big.Rat).Mul(new(big.Rat).Abs(value), new(big.Rat).SetInt(scale))
	quotient, remainder := new(big.Int).QuoRem(scaled.Num(), scaled.Denom(), new(big.Int))
	twice := new(big.Int).Mul(remainder, big.NewInt(2))
	switch twice.Cmp(scaled.Denom()) {
	case 1:
		quotient.Add(quotient, big.NewInt(1))
	case 0:
		if quotient.Bit(0) == 1 {
			quotient.Add(quotient, big.NewInt(1))
		}
	}
	digits := quotient.String()
	if len(digits) <= places {
		digits = strings.Repeat("0", places-len(digits)+1) + digits
	}
	text := digits[:len(digits)-places] + "." + digits[len(digits)-places:]
	if negative {
		text = "-" + text
	}
	return text
}
