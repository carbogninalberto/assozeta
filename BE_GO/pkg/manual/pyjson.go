package manual

import (
	"errors"
	"math"
	"math/big"
	"strconv"
	"strings"
	"unicode/utf16"
	"unicode/utf8"
)

// Capture reports are read with Python's json.loads and written with
// json.dumps(indent=2): objects keep their first-key position with the last
// value, numbers keep the int/float distinction, NaN and Infinity are accepted
// and output escapes non-ASCII text. These helpers reproduce that behaviour for
// compare_manuale_captures; lone UTF-16 surrogates, which Python keeps in str
// values, are refused.

var errCapturesJSON = errors.New("capture report is not valid JSON")

// capturesInt is a Python int; float64 values are Python floats.
type capturesInt struct{ value *big.Int }

// capturesObject is a Python dict decoded from JSON.
type capturesObject struct {
	keys   []string
	values map[string]any
}

func (o *capturesObject) get(key string) (any, bool) {
	value, ok := o.values[key]
	return value, ok
}

func (o *capturesObject) set(key string, value any) {
	if _, ok := o.values[key]; !ok {
		o.keys = append(o.keys, key)
	}
	o.values[key] = value
}

func newCapturesObject() *capturesObject { return &capturesObject{values: map[string]any{}} }

// Python's json module shares one NaN object, so NaN inside containers is
// equal by identity while a direct NaN comparison is false.
type capturesDecoder struct {
	data  string
	at    int
	depth int
}

func capturesDecode(raw []byte) (any, error) {
	if !utf8.Valid(raw) {
		return nil, errCapturesJSON
	}
	// Path.read_text uses universal newlines; a CR is whitespace outside
	// strings and an invalid control character inside them either way.
	d := &capturesDecoder{data: string(raw)}
	d.space()
	value, err := d.value()
	if err != nil {
		return nil, err
	}
	d.space()
	if d.at != len(d.data) {
		return nil, errCapturesJSON
	}
	return value, nil
}

func (d *capturesDecoder) space() {
	for d.at < len(d.data) {
		switch d.data[d.at] {
		case ' ', '\t', '\n', '\r':
			d.at++
		default:
			return
		}
	}
}

func (d *capturesDecoder) literal(word string) bool {
	if strings.HasPrefix(d.data[d.at:], word) {
		d.at += len(word)
		return true
	}
	return false
}

func (d *capturesDecoder) value() (any, error) {
	if d.at >= len(d.data) {
		return nil, errCapturesJSON
	}
	switch c := d.data[d.at]; {
	case c == '"':
		return d.text()
	case c == '{':
		return d.object()
	case c == '[':
		return d.array()
	case d.literal("null"):
		return nil, nil
	case d.literal("true"):
		return true, nil
	case d.literal("false"):
		return false, nil
	case d.literal("NaN"):
		return math.NaN(), nil
	case d.literal("Infinity"):
		return math.Inf(1), nil
	case d.literal("-Infinity"):
		return math.Inf(-1), nil
	case c == '-' || c >= '0' && c <= '9':
		return d.number()
	}
	return nil, errCapturesJSON
}

func (d *capturesDecoder) digits() int {
	start := d.at
	for d.at < len(d.data) && d.data[d.at] >= '0' && d.data[d.at] <= '9' {
		d.at++
	}
	return d.at - start
}

// number follows json.scanner.NUMBER_RE: an integer part, then an optional
// fraction and exponent that are taken only when complete.
func (d *capturesDecoder) number() (any, error) {
	start := d.at
	if d.data[d.at] == '-' {
		d.at++
	}
	if d.at < len(d.data) && d.data[d.at] == '0' {
		d.at++
	} else if d.at < len(d.data) && d.data[d.at] >= '1' && d.data[d.at] <= '9' {
		d.digits()
	} else {
		return nil, errCapturesJSON
	}
	integer := d.at
	float := false
	if d.at+1 < len(d.data) && d.data[d.at] == '.' && d.data[d.at+1] >= '0' && d.data[d.at+1] <= '9' {
		d.at++
		d.digits()
		float = true
	}
	if d.at < len(d.data) && (d.data[d.at] == 'e' || d.data[d.at] == 'E') {
		mark := d.at
		d.at++
		if d.at < len(d.data) && (d.data[d.at] == '+' || d.data[d.at] == '-') {
			d.at++
		}
		if d.digits() == 0 {
			d.at = mark
		} else {
			float = true
		}
	}
	raw := d.data[start:d.at]
	if !float {
		// int() refuses more than 4300 digits.
		if integer-start-strings.Count(raw, "-") > 4300 {
			return nil, errCapturesJSON
		}
		n, ok := new(big.Int).SetString(raw, 10)
		if !ok {
			return nil, errCapturesJSON
		}
		return capturesInt{n}, nil
	}
	n, err := strconv.ParseFloat(raw, 64)
	if err != nil && !errors.Is(err, strconv.ErrRange) {
		return nil, errCapturesJSON
	}
	return n, nil
}

func (d *capturesDecoder) hex4() (rune, bool) {
	if d.at+4 > len(d.data) {
		return 0, false
	}
	n, err := strconv.ParseUint(d.data[d.at:d.at+4], 16, 16)
	if err != nil {
		return 0, false
	}
	d.at += 4
	return rune(n), true
}

func (d *capturesDecoder) text() (string, error) {
	d.at++
	var out strings.Builder
	for {
		if d.at >= len(d.data) {
			return "", errCapturesJSON
		}
		c := d.data[d.at]
		switch {
		case c == '"':
			d.at++
			return out.String(), nil
		case c < 0x20:
			return "", errCapturesJSON
		case c != '\\':
			out.WriteByte(c)
			d.at++
			continue
		}
		d.at++
		if d.at >= len(d.data) {
			return "", errCapturesJSON
		}
		escape := d.data[d.at]
		d.at++
		switch escape {
		case '"', '\\', '/':
			out.WriteByte(escape)
		case 'b':
			out.WriteByte('\b')
		case 'f':
			out.WriteByte('\f')
		case 'n':
			out.WriteByte('\n')
		case 'r':
			out.WriteByte('\r')
		case 't':
			out.WriteByte('\t')
		case 'u':
			r, ok := d.hex4()
			if !ok {
				return "", errCapturesJSON
			}
			if utf16.IsSurrogate(r) {
				if r >= 0xdc00 || !strings.HasPrefix(d.data[d.at:], `\u`) {
					return "", errCapturesJSON
				}
				d.at += 2
				low, ok := d.hex4()
				if !ok || low < 0xdc00 || low > 0xdfff {
					return "", errCapturesJSON
				}
				r = utf16.DecodeRune(r, low)
			}
			out.WriteRune(r)
		default:
			return "", errCapturesJSON
		}
	}
}

func (d *capturesDecoder) nest() error {
	// Python's recursion limit is not reproduced; very deep documents are refused.
	d.depth++
	if d.depth > 900 {
		return errCapturesJSON
	}
	return nil
}

func (d *capturesDecoder) object() (any, error) {
	if err := d.nest(); err != nil {
		return nil, err
	}
	defer func() { d.depth-- }()
	d.at++
	result := newCapturesObject()
	d.space()
	if d.at < len(d.data) && d.data[d.at] == '}' {
		d.at++
		return result, nil
	}
	for {
		if d.at >= len(d.data) || d.data[d.at] != '"' {
			return nil, errCapturesJSON
		}
		key, err := d.text()
		if err != nil {
			return nil, err
		}
		d.space()
		if d.at >= len(d.data) || d.data[d.at] != ':' {
			return nil, errCapturesJSON
		}
		d.at++
		d.space()
		value, err := d.value()
		if err != nil {
			return nil, err
		}
		result.set(key, value)
		d.space()
		if d.at >= len(d.data) {
			return nil, errCapturesJSON
		}
		if d.data[d.at] == '}' {
			d.at++
			return result, nil
		}
		if d.data[d.at] != ',' {
			return nil, errCapturesJSON
		}
		d.at++
		d.space()
	}
}

func (d *capturesDecoder) array() (any, error) {
	if err := d.nest(); err != nil {
		return nil, err
	}
	defer func() { d.depth-- }()
	d.at++
	result := []any{}
	d.space()
	if d.at < len(d.data) && d.data[d.at] == ']' {
		d.at++
		return result, nil
	}
	for {
		value, err := d.value()
		if err != nil {
			return nil, err
		}
		result = append(result, value)
		d.space()
		if d.at >= len(d.data) {
			return nil, errCapturesJSON
		}
		if d.data[d.at] == ']' {
			d.at++
			return result, nil
		}
		if d.data[d.at] != ',' {
			return nil, errCapturesJSON
		}
		d.at++
		d.space()
	}
}

// capturesNumber returns an exact rational for Python's numeric tower
// (bool, int, float); ok is false for other values and for NaN/Infinity.
func capturesNumber(value any) (*big.Rat, bool) {
	switch v := value.(type) {
	case bool:
		if v {
			return big.NewRat(1, 1), true
		}
		return new(big.Rat), true
	case capturesInt:
		return new(big.Rat).SetInt(v.value), true
	case float64:
		if math.IsNaN(v) || math.IsInf(v, 0) {
			return nil, false
		}
		return new(big.Rat).SetFloat64(v), true
	}
	return nil, false
}

func capturesNumeric(value any) bool {
	switch value.(type) {
	case bool, capturesInt, float64:
		return true
	}
	return false
}

// capturesEqual is Python ==; nested comparisons use PyObject_RichCompareBool,
// whose identity shortcut makes the shared json NaN equal to itself.
func capturesEqual(a, b any, nested bool) bool {
	if capturesNumeric(a) && capturesNumeric(b) {
		x, xf := a.(float64)
		y, yf := b.(float64)
		if xf && math.IsNaN(x) || yf && math.IsNaN(y) {
			return nested && xf && yf && math.IsNaN(x) && math.IsNaN(y)
		}
		if xf && math.IsInf(x, 0) || yf && math.IsInf(y, 0) {
			return xf && yf && x == y
		}
		left, _ := capturesNumber(a)
		right, _ := capturesNumber(b)
		return left.Cmp(right) == 0
	}
	switch x := a.(type) {
	case nil:
		return b == nil
	case string:
		y, ok := b.(string)
		return ok && x == y
	case []any:
		y, ok := b.([]any)
		if !ok || len(x) != len(y) {
			return false
		}
		for i := range x {
			if !capturesEqual(x[i], y[i], true) {
				return false
			}
		}
		return true
	case *capturesObject:
		y, ok := b.(*capturesObject)
		if !ok || len(x.keys) != len(y.keys) {
			return false
		}
		for _, key := range x.keys {
			other, present := y.values[key]
			if !present || !capturesEqual(x.values[key], other, true) {
				return false
			}
		}
		return true
	}
	return false
}

// capturesTruthy is Python bool(value).
func capturesTruthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case capturesInt:
		return v.value.Sign() != 0
	case float64:
		return v != 0
	case string:
		return v != ""
	case []any:
		return len(v) > 0
	case *capturesObject:
		return len(v.keys) > 0
	}
	return true
}

// capturesFloat is float.__repr__.
func capturesFloat(value float64) string {
	switch {
	case math.IsNaN(value):
		return "NaN"
	case math.IsInf(value, 1):
		return "Infinity"
	case math.IsInf(value, -1):
		return "-Infinity"
	}
	scientific := strconv.FormatFloat(value, 'e', -1, 64)
	exponent, _ := strconv.Atoi(scientific[strings.IndexByte(scientific, 'e')+1:])
	if exponent < -4 || exponent >= 16 {
		return scientific
	}
	fixed := strconv.FormatFloat(value, 'f', -1, 64)
	if !strings.Contains(fixed, ".") {
		fixed += ".0"
	}
	return fixed
}

// capturesRound is round(value, digits) for a finite float.
func capturesRound(value float64, digits int) float64 {
	rounded, _ := strconv.ParseFloat(strconv.FormatFloat(value, 'f', digits, 64), 64)
	return rounded
}

func capturesQuote(out *strings.Builder, value string) {
	const hex = "0123456789abcdef"
	unit := func(r rune) {
		out.WriteString(`\u`)
		out.WriteByte(hex[r>>12&15])
		out.WriteByte(hex[r>>8&15])
		out.WriteByte(hex[r>>4&15])
		out.WriteByte(hex[r&15])
	}
	out.WriteByte('"')
	for _, r := range value {
		switch {
		case r == '"':
			out.WriteString(`\"`)
		case r == '\\':
			out.WriteString(`\\`)
		case r == '\n':
			out.WriteString(`\n`)
		case r == '\r':
			out.WriteString(`\r`)
		case r == '\t':
			out.WriteString(`\t`)
		case r == '\b':
			out.WriteString(`\b`)
		case r == '\f':
			out.WriteString(`\f`)
		case r >= ' ' && r <= '~':
			out.WriteRune(r)
		case r > 0xffff:
			high, low := utf16.EncodeRune(r)
			unit(high)
			unit(low)
		default:
			unit(r)
		}
	}
	out.WriteByte('"')
}

// capturesDumps is json.dumps(value, indent=2) with ensure_ascii.
func capturesDumps(value any) string {
	var out strings.Builder
	var write func(any, int)
	newline := func(level int) {
		out.WriteByte('\n')
		out.WriteString(strings.Repeat(" ", 2*level))
	}
	write = func(value any, level int) {
		switch v := value.(type) {
		case nil:
			out.WriteString("null")
		case bool:
			out.WriteString(strconv.FormatBool(v))
		case capturesInt:
			out.WriteString(v.value.String())
		case int:
			out.WriteString(strconv.Itoa(v))
		case float64:
			out.WriteString(capturesFloat(v))
		case string:
			capturesQuote(&out, v)
		case []any:
			if len(v) == 0 {
				out.WriteString("[]")
				return
			}
			out.WriteByte('[')
			for i, item := range v {
				if i > 0 {
					out.WriteByte(',')
				}
				newline(level + 1)
				write(item, level+1)
			}
			newline(level)
			out.WriteByte(']')
		case *capturesObject:
			if len(v.keys) == 0 {
				out.WriteString("{}")
				return
			}
			out.WriteByte('{')
			for i, key := range v.keys {
				if i > 0 {
					out.WriteByte(',')
				}
				newline(level + 1)
				capturesQuote(&out, key)
				out.WriteString(": ")
				write(v.values[key], level+1)
			}
			newline(level)
			out.WriteByte('}')
		}
	}
	write(value, 0)
	return out.String()
}
