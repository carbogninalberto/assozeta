package instanceprobe

import (
	"errors"
	"math"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrJSON is the ValueError json.loads raises (JSONDecodeError,
// UnicodeDecodeError, or the int() digit limit).
var ErrJSON = errors.New("instanceprobe: invalid JSON document")

// ErrJSONEncoding marks a document json.loads would decode as UTF-16 or
// UTF-32 (detect_encoding); this port does not read those.
var ErrJSONEncoding = refusal.New("instanceprobe: JSON text encoding not ported")

// Int is a Python int parsed from JSON (decimal digits, optional minus).
type Int string

// LoadJSON mirrors json.loads(raw) for a bytes argument: detect_encoding
// (UTF-8, or UTF-8 with a BOM), decode with 'surrogatepass', then the C
// scanner (strict control characters, NaN/Infinity/-Infinity, ints with
// Python's 4300-digit limit, floats through float()). Values are nil, bool,
// Int, float64, string, []any and map[string]any (the last duplicate key
// wins). Strings keep a lone surrogate as its three-byte (WTF-8) encoding;
// HasSurrogate reports them.
func LoadJSON(raw []byte) (any, error) {
	switch {
	case hasPrefix(raw, "\x00\x00\xfe\xff"), hasPrefix(raw, "\xff\xfe\x00\x00"), hasPrefix(raw, "\xfe\xff"), hasPrefix(raw, "\xff\xfe"):
		return nil, ErrJSONEncoding
	case hasPrefix(raw, "\xef\xbb\xbf"):
		raw = raw[3:]
	case len(raw) >= 4 && (raw[0] == 0 || raw[1] == 0):
		return nil, ErrJSONEncoding
	case len(raw) == 2 && (raw[0] == 0 || raw[1] == 0):
		return nil, ErrJSONEncoding
	}
	if !validSurrogatePass(raw) {
		return nil, ErrJSON
	}
	p := &jsonParser{data: string(raw)}
	p.space()
	value, err := p.value(0)
	if err != nil {
		return nil, err
	}
	p.space()
	if p.pos != len(p.data) {
		return nil, ErrJSON
	}
	return value, nil
}

func hasPrefix(raw []byte, prefix string) bool { return strings.HasPrefix(string(raw), prefix) }

// validSurrogatePass is the 'utf-8' codec with 'surrogatepass': valid UTF-8
// plus the encodings of U+D800..U+DFFF.
func validSurrogatePass(raw []byte) bool {
	for i := 0; i < len(raw); {
		if raw[i] < 0x80 {
			i++
			continue
		}
		if raw[i] == 0xED && i+2 < len(raw) && raw[i+1] >= 0xA0 && raw[i+1] <= 0xBF && raw[i+2] >= 0x80 && raw[i+2] <= 0xBF {
			i += 3
			continue
		}
		r, size := utf8.DecodeRune(raw[i:])
		if r == utf8.RuneError && size <= 1 {
			return false
		}
		i += size
	}
	return true
}

// HasSurrogate reports a lone surrogate kept by LoadJSON (or surrogatepass).
func HasSurrogate(s string) bool {
	for i := 0; i+2 < len(s); i++ {
		if s[i] == 0xED && s[i+1] >= 0xA0 && s[i+1] <= 0xBF {
			return true
		}
	}
	return false
}

const maxJSONDepth = 900

type jsonParser struct {
	data string
	pos  int
}

func (p *jsonParser) space() {
	for p.pos < len(p.data) {
		switch p.data[p.pos] {
		case ' ', '\t', '\n', '\r':
			p.pos++
		default:
			return
		}
	}
}

func (p *jsonParser) literal(word string) bool {
	if strings.HasPrefix(p.data[p.pos:], word) {
		p.pos += len(word)
		return true
	}
	return false
}

func (p *jsonParser) value(depth int) (any, error) {
	if depth > maxJSONDepth || p.pos >= len(p.data) {
		return nil, ErrJSON
	}
	switch c := p.data[p.pos]; {
	case c == '"':
		p.pos++
		return p.str()
	case c == '{':
		p.pos++
		return p.object(depth)
	case c == '[':
		p.pos++
		return p.array(depth)
	case p.literal("null"):
		return nil, nil
	case p.literal("true"):
		return true, nil
	case p.literal("false"):
		return false, nil
	case p.literal("NaN"):
		return math.NaN(), nil
	case p.literal("Infinity"):
		return math.Inf(1), nil
	case p.literal("-Infinity"):
		return math.Inf(-1), nil
	case c == '-' || (c >= '0' && c <= '9'):
		return p.number()
	}
	return nil, ErrJSON
}

func digits(s string, i int) int {
	for i < len(s) && s[i] >= '0' && s[i] <= '9' {
		i++
	}
	return i
}

// number is NUMBER_RE: (-?(?:0|[1-9][0-9]*))(\.[0-9]+)?([eE][-+]?[0-9]+)?
func (p *jsonParser) number() (any, error) {
	s, start := p.data, p.pos
	i := start
	if s[i] == '-' {
		i++
	}
	if i >= len(s) || s[i] < '0' || s[i] > '9' {
		return nil, ErrJSON
	}
	if s[i] == '0' {
		i++
	} else {
		i = digits(s, i)
	}
	integerEnd, isFloat := i, false
	if i+1 < len(s) && s[i] == '.' && s[i+1] >= '0' && s[i+1] <= '9' {
		i, isFloat = digits(s, i+1), true
	}
	if i < len(s) && (s[i] == 'e' || s[i] == 'E') {
		j := i + 1
		if j < len(s) && (s[j] == '+' || s[j] == '-') {
			j++
		}
		if j < len(s) && s[j] >= '0' && s[j] <= '9' {
			i, isFloat = digits(s, j), true
		}
	}
	p.pos = i
	if !isFloat {
		text := s[start:integerEnd]
		if len(strings.TrimPrefix(text, "-")) > 4300 {
			return nil, ErrJSON
		}
		return Int(text), nil
	}
	f, err := strconv.ParseFloat(s[start:i], 64)
	if err != nil && !errors.Is(err, strconv.ErrRange) {
		return nil, ErrJSON
	}
	return f, nil
}

func hex4(s string) (rune, bool) {
	if len(s) < 4 {
		return 0, false
	}
	value, err := strconv.ParseUint(s[:4], 16, 32)
	if err != nil || strings.ContainsAny(s[:4], "+-_xX") {
		return 0, false
	}
	return rune(value), true
}

func appendWTF8(out *strings.Builder, r rune) {
	if r >= 0xD800 && r <= 0xDFFF {
		out.WriteByte(0xED)
		out.WriteByte(byte(0xA0 | (r>>6)&0x1F))
		out.WriteByte(byte(0x80 | r&0x3F))
		return
	}
	out.WriteRune(r)
}

// str is py_scanstring with strict=True, after the opening quote.
func (p *jsonParser) str() (string, error) {
	var out strings.Builder
	s := p.data
	for {
		if p.pos >= len(s) {
			return "", ErrJSON
		}
		c := s[p.pos]
		switch {
		case c == '"':
			p.pos++
			return out.String(), nil
		case c < 0x20:
			return "", ErrJSON
		case c != '\\':
			out.WriteByte(c)
			p.pos++
			continue
		}
		p.pos++
		if p.pos >= len(s) {
			return "", ErrJSON
		}
		escape := s[p.pos]
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
			r, ok := hex4(s[p.pos:])
			if !ok {
				return "", ErrJSON
			}
			p.pos += 4
			if r >= 0xD800 && r <= 0xDBFF && strings.HasPrefix(s[p.pos:], `\u`) {
				if low, ok := hex4(s[p.pos+2:]); ok && low >= 0xDC00 && low <= 0xDFFF {
					p.pos += 6
					r = 0x10000 + (r-0xD800)<<10 + (low - 0xDC00)
				}
			}
			appendWTF8(&out, r)
		default:
			return "", ErrJSON
		}
	}
}

func (p *jsonParser) object(depth int) (any, error) {
	result := map[string]any{}
	p.space()
	if p.pos < len(p.data) && p.data[p.pos] == '}' {
		p.pos++
		return result, nil
	}
	for {
		if p.pos >= len(p.data) || p.data[p.pos] != '"' {
			return nil, ErrJSON
		}
		p.pos++
		key, err := p.str()
		if err != nil {
			return nil, err
		}
		p.space()
		if p.pos >= len(p.data) || p.data[p.pos] != ':' {
			return nil, ErrJSON
		}
		p.pos++
		p.space()
		value, err := p.value(depth + 1)
		if err != nil {
			return nil, err
		}
		result[key] = value
		p.space()
		if p.pos >= len(p.data) {
			return nil, ErrJSON
		}
		switch p.data[p.pos] {
		case '}':
			p.pos++
			return result, nil
		case ',':
			p.pos++
			p.space()
		default:
			return nil, ErrJSON
		}
	}
}

func (p *jsonParser) array(depth int) (any, error) {
	result := []any{}
	p.space()
	if p.pos < len(p.data) && p.data[p.pos] == ']' {
		p.pos++
		return result, nil
	}
	for {
		value, err := p.value(depth + 1)
		if err != nil {
			return nil, err
		}
		result = append(result, value)
		p.space()
		if p.pos >= len(p.data) {
			return nil, ErrJSON
		}
		switch p.data[p.pos] {
		case ']':
			p.pos++
			return result, nil
		case ',':
			p.pos++
			p.space()
		default:
			return nil, ErrJSON
		}
	}
}

// Truthy is Python truthiness of a LoadJSON value.
func Truthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case Int:
		return strings.Trim(string(v), "-0") != ""
	case float64:
		return v != 0
	case string:
		return v != ""
	case []any:
		return len(v) > 0
	case map[string]any:
		return len(v) > 0
	}
	return true
}
