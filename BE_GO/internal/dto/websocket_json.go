package dto

import (
	"encoding/json"
	"errors"
	"math"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// ErrPythonJSON is any value Python's json.loads rejects (JSONDecodeError,
// the 4300-digit integer limit or excessive nesting).
var ErrPythonJSON = errors.New("python json.loads failed")

// pythonJSONMaxDepth bounds nesting like the interpreter's recursion guard.
const pythonJSONMaxDepth = 10000

// ParsePythonJSON is Python's json.loads with its C scanner: NaN, Infinity
// and -Infinity literals (as float64), integers and floats kept as their
// json.Number text, objects in first-key order with the last duplicate value,
// lone UTF-16 surrogate escapes preserved as WTF-8, and only " \t\n\r" as
// whitespace. Values work with PythonJSONDumps and PythonStr.
func ParsePythonJSON(text string) (any, error) {
	p := pythonJSONParser{text: text}
	p.space()
	value, err := p.value(0)
	if err != nil {
		return nil, err
	}
	p.space()
	if p.pos != len(p.text) {
		return nil, ErrPythonJSON
	}
	return value, nil
}

type pythonJSONParser struct {
	text string
	pos  int
}

func (p *pythonJSONParser) space() {
	for p.pos < len(p.text) && strings.IndexByte(" \t\n\r", p.text[p.pos]) >= 0 {
		p.pos++
	}
}

func (p *pythonJSONParser) literal(word string) bool {
	if strings.HasPrefix(p.text[p.pos:], word) {
		p.pos += len(word)
		return true
	}
	return false
}

func (p *pythonJSONParser) value(depth int) (any, error) {
	if depth > pythonJSONMaxDepth || p.pos >= len(p.text) {
		return nil, ErrPythonJSON
	}
	switch ch := p.text[p.pos]; {
	case ch == '"':
		p.pos++
		return p.string()
	case ch == '{':
		p.pos++
		return p.object(depth)
	case ch == '[':
		p.pos++
		return p.array(depth)
	case ch == 'n' && p.literal("null"):
		return nil, nil
	case ch == 't' && p.literal("true"):
		return true, nil
	case ch == 'f' && p.literal("false"):
		return false, nil
	}
	if number, ok := p.number(); ok {
		return number, nil
	}
	switch {
	case p.literal("NaN"):
		return math.NaN(), nil
	case p.literal("Infinity"):
		return math.Inf(1), nil
	case p.literal("-Infinity"):
		return math.Inf(-1), nil
	}
	return nil, ErrPythonJSON
}

func isDigit(ch byte) bool { return ch >= '0' && ch <= '9' }

// number matches (-?(?:0|[1-9]\d*))(\.\d+)?([eE][-+]?\d+)? with ASCII digits.
func (p *pythonJSONParser) number() (any, bool) {
	start, i := p.pos, p.pos
	if i < len(p.text) && p.text[i] == '-' {
		i++
	}
	switch {
	case i < len(p.text) && p.text[i] == '0':
		i++
	case i < len(p.text) && p.text[i] >= '1' && p.text[i] <= '9':
		for i < len(p.text) && isDigit(p.text[i]) {
			i++
		}
	default:
		return nil, false
	}
	integer := true
	if i+1 < len(p.text) && p.text[i] == '.' && isDigit(p.text[i+1]) {
		integer = false
		i += 2
		for i < len(p.text) && isDigit(p.text[i]) {
			i++
		}
	}
	if i < len(p.text) && (p.text[i] == 'e' || p.text[i] == 'E') {
		j := i + 1
		if j < len(p.text) && (p.text[j] == '+' || p.text[j] == '-') {
			j++
		}
		if j < len(p.text) && isDigit(p.text[j]) {
			for j < len(p.text) && isDigit(p.text[j]) {
				j++
			}
			integer, i = false, j
		}
	}
	token := p.text[start:i]
	if integer && len(strings.TrimPrefix(token, "-")) > 4300 {
		// int() refuses more than sys.get_int_max_str_digits() digits.
		return nil, false
	}
	p.pos = i
	return json.Number(token), true
}

func (p *pythonJSONParser) hex4() (rune, bool) {
	if p.pos+4 > len(p.text) {
		return 0, false
	}
	value, err := strconv.ParseUint(p.text[p.pos:p.pos+4], 16, 32)
	if err != nil {
		return 0, false
	}
	p.pos += 4
	return rune(value), true
}

func appendSurrogate(out *strings.Builder, r rune) {
	// WTF-8, as pythonCodePoints decodes it.
	out.WriteByte(0xed)
	out.WriteByte(byte(0x80 | (r>>6)&0x3f))
	out.WriteByte(byte(0x80 | r&0x3f))
}

func (p *pythonJSONParser) string() (string, error) {
	var out strings.Builder
	for {
		if p.pos >= len(p.text) {
			return "", ErrPythonJSON
		}
		ch := p.text[p.pos]
		switch {
		case ch == '"':
			p.pos++
			return out.String(), nil
		case ch < 0x20:
			return "", ErrPythonJSON
		case ch != '\\':
			_, size := utf8.DecodeRuneInString(p.text[p.pos:])
			out.WriteString(p.text[p.pos : p.pos+size])
			p.pos += size
			continue
		}
		p.pos++
		if p.pos >= len(p.text) {
			return "", ErrPythonJSON
		}
		escape := p.text[p.pos]
		p.pos++
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
			r, ok := p.hex4()
			if !ok {
				return "", ErrPythonJSON
			}
			if r >= 0xd800 && r <= 0xdbff && strings.HasPrefix(p.text[p.pos:], `\u`) {
				save := p.pos
				p.pos += 2
				if low, ok := p.hex4(); ok && low >= 0xdc00 && low <= 0xdfff {
					out.WriteRune(0x10000 + (r-0xd800)<<10 + (low - 0xdc00))
					continue
				}
				p.pos = save
			}
			if r >= 0xd800 && r <= 0xdfff {
				appendSurrogate(&out, r)
			} else {
				out.WriteRune(r)
			}
		default:
			return "", ErrPythonJSON
		}
	}
}

func (p *pythonJSONParser) object(depth int) (any, error) {
	object := domain.RequestObject{}
	positions := map[string]int{}
	p.space()
	if p.pos < len(p.text) && p.text[p.pos] == '}' {
		p.pos++
		return object, nil
	}
	for {
		if p.pos >= len(p.text) || p.text[p.pos] != '"' {
			return nil, ErrPythonJSON
		}
		p.pos++
		key, err := p.string()
		if err != nil {
			return nil, err
		}
		p.space()
		if p.pos >= len(p.text) || p.text[p.pos] != ':' {
			return nil, ErrPythonJSON
		}
		p.pos++
		p.space()
		value, err := p.value(depth + 1)
		if err != nil {
			return nil, err
		}
		if index, exists := positions[key]; exists {
			object[index].Value = value
		} else {
			positions[key] = len(object)
			object = append(object, domain.RequestField{Name: key, Value: value})
		}
		p.space()
		if p.pos >= len(p.text) {
			return nil, ErrPythonJSON
		}
		switch p.text[p.pos] {
		case '}':
			p.pos++
			return object, nil
		case ',':
			p.pos++
			p.space()
		default:
			return nil, ErrPythonJSON
		}
	}
}

func (p *pythonJSONParser) array(depth int) (any, error) {
	array := []any{}
	p.space()
	if p.pos < len(p.text) && p.text[p.pos] == ']' {
		p.pos++
		return array, nil
	}
	for {
		value, err := p.value(depth + 1)
		if err != nil {
			return nil, err
		}
		array = append(array, value)
		p.space()
		if p.pos >= len(p.text) {
			return nil, ErrPythonJSON
		}
		switch p.text[p.pos] {
		case ']':
			p.pos++
			return array, nil
		case ',':
			p.pos++
			p.space()
		default:
			return nil, ErrPythonJSON
		}
	}
}

// PythonGet is dict.get(key) for a parsed object; ok is false for non-objects
// (where Python raises AttributeError).
func PythonGet(value any, key string) (any, bool) {
	object, ok := value.(domain.RequestObject)
	if !ok {
		return nil, false
	}
	for _, field := range object {
		if field.Name == key {
			return field.Value, true
		}
	}
	return nil, true
}
