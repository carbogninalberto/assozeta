package instanceprobe

import (
	"errors"
	"math"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
)

// ErrJSON is the ValueError json.loads raises (JSONDecodeError,
// UnicodeDecodeError, or the int() digit limit).
var ErrJSON = errors.New("instanceprobe: invalid JSON document")

// Int is a Python int parsed from JSON (decimal digits, optional minus).
type Int string

// LoadJSON mirrors json.loads(raw) for a bytes argument: detect_encoding
// (UTF-8, UTF-8 with a BOM, UTF-16 or UTF-32), decode with 'surrogatepass',
// then the C scanner (strict control characters, NaN/Infinity/-Infinity, ints
// with Python's 4300-digit limit, floats through float()). Values are nil,
// bool, Int, float64, string, []any and map[string]any (the last duplicate key
// wins). Strings keep a lone surrogate as its three-byte (WTF-8) encoding;
// HasSurrogate reports them. loadOrderedJSON reads objects as Dicts instead,
// keeping their source key order.
func LoadJSON(raw []byte) (any, error) { return loadJSON(raw, false) }

// loadOrderedJSON is json.loads(raw) for the release feed: an object is a Dict
// in source key order, a repeated key keeping its first position and taking
// its last value.
func loadOrderedJSON(raw []byte) (any, error) { return loadJSON(raw, true) }

func loadJSON(raw []byte, ordered bool) (any, error) {
	switch encoding := detectEncoding(raw); encoding {
	case "utf-8":
	case "utf-8-sig":
		raw = raw[3:]
	default:
		text, ok := decodeWide(raw, encoding)
		if !ok {
			return nil, ErrJSON // UnicodeDecodeError, a ValueError
		}
		raw = []byte(text)
	}
	if !validSurrogatePass(raw) {
		return nil, ErrJSON
	}
	p := &jsonParser{data: string(raw), ordered: ordered}
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

// detectEncoding is json.detect_encoding.
func detectEncoding(b []byte) string {
	switch {
	case hasPrefix(b, "\x00\x00\xfe\xff"), hasPrefix(b, "\xff\xfe\x00\x00"):
		return "utf-32"
	case hasPrefix(b, "\xfe\xff"), hasPrefix(b, "\xff\xfe"):
		return "utf-16"
	case hasPrefix(b, "\xef\xbb\xbf"):
		return "utf-8-sig"
	case len(b) >= 4 && b[0] == 0:
		if b[1] != 0 {
			return "utf-16-be"
		}
		return "utf-32-be"
	case len(b) >= 4 && b[1] == 0:
		if b[2] != 0 || b[3] != 0 {
			return "utf-16-le"
		}
		return "utf-32-le"
	case len(b) == 2 && b[0] == 0:
		return "utf-16-be"
	case len(b) == 2 && b[1] == 0:
		return "utf-16-le"
	}
	return "utf-8"
}

// decodeWide is bytes.decode(encoding, 'surrogatepass') for the UTF-16 and
// UTF-32 codecs detect_encoding names, as WTF-8 (a lone surrogate keeps its
// three-byte form). 'utf-16' and 'utf-32' read the byte order from the BOM
// and drop it; the explicit -be/-le codecs keep a U+FEFF as a character. ok
// false is the UnicodeDecodeError: truncated data, or a UTF-32 unit beyond
// U+10FFFF.
func decodeWide(b []byte, encoding string) (string, bool) {
	width, big := 2, false
	switch encoding {
	case "utf-32":
		width, big, b = 4, b[0] == 0, b[4:]
	case "utf-16":
		big, b = b[0] == 0xfe, b[2:]
	case "utf-32-be":
		width, big = 4, true
	case "utf-32-le":
		width = 4
	case "utf-16-be":
		big = true
	}
	if len(b)%width != 0 {
		return "", false
	}
	unit := func(i int) rune {
		var u uint32
		for k := 0; k < width; k++ {
			shift := 8 * k
			if big {
				shift = 8 * (width - 1 - k)
			}
			u |= uint32(b[i+k]) << shift
		}
		return rune(u)
	}
	runes := make([]rune, 0, len(b)/width)
	for i := 0; i < len(b); i += width {
		r := unit(i)
		if width == 4 {
			if uint32(r) > 0x10FFFF {
				return "", false
			}
			runes = append(runes, r)
			continue
		}
		if r >= 0xD800 && r <= 0xDBFF && i+2 < len(b) {
			if low := unit(i + 2); low >= 0xDC00 && low <= 0xDFFF {
				runes = append(runes, 0x10000+(r-0xD800)<<10+(low-0xDC00))
				i += 2
				continue
			}
		}
		runes = append(runes, r) // a lone surrogate passes ('surrogatepass')
	}
	return pystr.String(runes), true
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
	// ordered makes object() build a Dict in source key order (the release
	// feed) instead of a map.
	ordered bool
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
	var fields Dict
	index := map[string]int{}
	if p.ordered {
		fields = Dict{}
	}
	p.space()
	if p.pos < len(p.data) && p.data[p.pos] == '}' {
		p.pos++
		if p.ordered {
			return fields, nil
		}
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
		if p.ordered {
			// A Python dict keeps a repeated key at its first position and
			// takes the last value.
			if i, seen := index[key]; seen {
				fields[i].Value = value
			} else {
				index[key] = len(fields)
				fields = append(fields, DictField{Name: key, Value: value})
			}
		} else {
			result[key] = value
		}
		p.space()
		if p.pos >= len(p.data) {
			return nil, ErrJSON
		}
		switch p.data[p.pos] {
		case '}':
			p.pos++
			if p.ordered {
				return fields, nil
			}
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
	case Dict:
		return len(v) > 0
	case map[string]any:
		return len(v) > 0
	}
	return true
}
