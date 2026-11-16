package service

// json.loads(bytes) and BE/application/services/json_stream.iter_json_array.
//
// ImportValidator calls json.loads(zf.read(name)) on the members it decodes
// whole (manifest.json, data/01_sport_association.json) and streams
// data/02_users.json through iter_json_array; both decode the bytes through
// json.detect_encoding and bytes.decode(encoding, 'surrogatepass'), so the
// JSON grammar below runs on decoded code points and reports Python's
// JSONDecodeError positions (code points, not bytes). Integer literals keep
// Python's sys.int_max_str_digits limit (a ValueError, not a
// JSONDecodeError) and lone surrogates stay in strings (as WTF-8).

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pycodecs"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
)

// importJSONIntegerDigits is sys.int_max_str_digits: json.loads raises
// ValueError on a longer integer literal (Django's except json.JSONDecodeError
// does not catch it, so it is the generic exception text).
const importJSONIntegerDigits = 4300

// importJSONChunkSize is json_stream.DEFAULT_CHUNK_SIZE.
const importJSONChunkSize = 1024 * 1024

// importJSONDecodeError is json.JSONDecodeError. Error() is str(e):
// '<message>: line L column C (char P)', all counted in code points.
type importJSONDecodeError struct {
	message  string
	line     int
	column   int
	position int
}

func (e *importJSONDecodeError) Error() string {
	return fmt.Sprintf("%s: line %d column %d (char %d)", e.message, e.line, e.column, e.position)
}

// importJSONValueError is the ValueError json.loads' int() raises past
// sys.int_max_str_digits.
type importJSONValueError struct{ message string }

func (e *importJSONValueError) Error() string { return e.message }

// importJSONRecursionError is the RecursionError a deeply nested document
// raises inside the scanner.
type importJSONRecursionError struct{ object bool }

func (e *importJSONRecursionError) Error() string {
	kind := "array"
	if e.object {
		kind = "object"
	}
	return "maximum recursion depth exceeded while decoding a JSON " + kind + " from a unicode string"
}

// importJSONDiagnostic builds the JSONDecodeError for an offset of text.
func importJSONDiagnostic(message string, text []rune, pos int) *importJSONDecodeError {
	line, column := 1, pos+1
	for i := pos - 1; i >= 0; i-- {
		if text[i] == '\n' {
			line = 1 + strings.Count(string(text[:pos]), "\n")
			column = pos - i
			break
		}
	}
	return &importJSONDecodeError{message: message, line: line, column: column, position: pos}
}

// importJSONEncoding is json.detect_encoding.
func importJSONEncoding(b []byte) string {
	hasPrefix := func(prefix string) bool { return strings.HasPrefix(string(b), prefix) }
	switch {
	case hasPrefix("\x00\x00\xfe\xff"), hasPrefix("\xff\xfe\x00\x00"):
		return "utf-32"
	case hasPrefix("\xfe\xff"), hasPrefix("\xff\xfe"):
		return "utf-16"
	case hasPrefix("\xef\xbb\xbf"):
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

// importJSONParse is json.loads(data) for bytes: detect_encoding, then
// bytes.decode(encoding, 'surrogatepass'), then the scanner. Errors are the
// UnicodeDecodeError (*pycodecs.DecodeError), the JSONDecodeError
// (*importJSONDecodeError) or the int() ValueError (*importJSONValueError).
func importJSONParse(data []byte) (any, error) {
	encoding := importJSONEncoding(data)
	runes, err, ok := pycodecs.Decode(encoding, data, pycodecs.SurrogatePass)
	if !ok {
		return nil, errors.New("json: unported codec " + encoding)
	}
	if err != nil {
		return nil, err
	}
	p := &importJSONParser{text: runes}
	p.space()
	value, err := p.value(0)
	if err == nil {
		p.space()
		if p.pos != len(p.text) {
			err = importJSONDiagnostic("Extra data", p.text, p.pos)
		}
	}
	if err != nil {
		value = nil
	}
	return p.bandOutcome(value, err)
}

// importJSONParser is the strict scanner (JSONDecoder.scan_once, idx at the
// first code point of the value) over decoded text.
type importJSONParser struct {
	text []rune
	pos  int
	// deep is set once a container is entered at the band's first level:
	// from there the outcome depends on the request stack (see value),
	// unless it ends in the RecursionError every stack raises.
	deep     bool
	deepKind rune
}

// bandOutcome is the result of a parse that went past the recursion band's
// first level: CPython's limit is 9998 minus the C calls already on the
// request's stack, so such a document parses or fails depending on the
// caller, except when it reaches the floor (every stack raises) and every
// container entered from the band on is of one kind (RecursionError names
// it). The other outcomes are dto.ErrJSONRecursionBand.
func (p *importJSONParser) bandOutcome(value any, err error) (any, error) {
	if !p.deep {
		return value, err
	}
	var recursion *importJSONRecursionError
	if errors.As(err, &recursion) {
		return nil, err
	}
	return nil, dto.ErrJSONRecursionBand
}

func (p *importJSONParser) space() {
	for p.pos < len(p.text) {
		switch p.text[p.pos] {
		case ' ', '\t', '\r', '\n':
			p.pos++
		default:
			return
		}
	}
}

func (p *importJSONParser) take(c rune) bool {
	if p.pos < len(p.text) && p.text[p.pos] == c {
		p.pos++
		return true
	}
	return false
}

func (p *importJSONParser) literal(word string) bool {
	runes := []rune(word)
	if p.pos+len(runes) > len(p.text) {
		return false
	}
	for i, r := range runes {
		if p.text[p.pos+i] != r {
			return false
		}
	}
	p.pos += len(runes)
	return true
}

func importJSONDigit(r rune) bool { return r >= '0' && r <= '9' }

// value is scan_once at p.pos.
func (p *importJSONParser) value(depth int) (any, error) {
	p.space()
	if p.pos >= len(p.text) {
		return nil, importJSONDiagnostic("Expecting value", p.text, p.pos)
	}
	if c := p.text[p.pos]; c == '{' || c == '[' {
		// scan_once enters one recursive call per container (depth is the
		// enclosing containers): CPython 3.13 parses 9998 levels at the top of
		// a thread and fewer under C calls already on the request's stack.
		if level := depth + 1; level >= dto.StoredJSONRecursionBand {
			if !p.deep {
				p.deep, p.deepKind = true, c
			} else if c != p.deepKind {
				return nil, dto.ErrJSONRecursionBand // the RecursionError text depends on the stack
			}
			if level >= dto.StoredJSONRecursionFloor {
				return nil, &importJSONRecursionError{object: c == '{'}
			}
		}
	}
	switch p.text[p.pos] {
	case '"':
		p.pos++
		return p.textValue()
	case '{':
		p.pos++
		return p.object(depth)
	case '[':
		p.pos++
		return p.array(depth)
	}
	switch {
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
	}
	start := p.pos
	if p.text[p.pos] == '-' || importJSONDigit(p.text[p.pos]) {
		return p.number()
	}
	return nil, importJSONDiagnostic("Expecting value", p.text, start)
}

// number is NUMBER_RE: (-?(?:0|[1-9][0-9]*))(\.[0-9]+)?([eE][-+]?[0-9]+)?
// A fraction or exponent makes a Python float (float(), overflow to inf);
// otherwise the literal stays a Python int (json.Number keeps its digits) and
// past 4300 digits it is the int() ValueError.
func (p *importJSONParser) number() (any, error) {
	start := p.pos
	i := start
	if p.text[i] == '-' {
		i++
	}
	if i >= len(p.text) || !importJSONDigit(p.text[i]) {
		return nil, importJSONDiagnostic("Expecting value", p.text, start)
	}
	if p.text[i] == '0' {
		i++
	} else {
		for i < len(p.text) && importJSONDigit(p.text[i]) {
			i++
		}
	}
	integerEnd, isFloat := i, false
	if i+1 < len(p.text) && p.text[i] == '.' && importJSONDigit(p.text[i+1]) {
		i++
		for i < len(p.text) && importJSONDigit(p.text[i]) {
			i++
		}
		isFloat = true
	}
	if i < len(p.text) && (p.text[i] == 'e' || p.text[i] == 'E') {
		j := i + 1
		if j < len(p.text) && (p.text[j] == '+' || p.text[j] == '-') {
			j++
		}
		if j < len(p.text) && importJSONDigit(p.text[j]) {
			i = j
			for i < len(p.text) && importJSONDigit(p.text[i]) {
				i++
			}
			isFloat = true
		}
	}
	p.pos = i
	if !isFloat {
		digits := integerEnd - start
		if p.text[start] == '-' {
			digits--
		}
		if digits > importJSONIntegerDigits {
			return nil, &importJSONValueError{message: fmt.Sprintf("Exceeds the limit (4300 digits) for integer string conversion: "+
				"value has %d digits; use sys.set_int_max_str_digits() to increase the limit", digits)}
		}
		return jsonNumber(p.text[start:integerEnd]), nil
	}
	text := string(p.text[start:i])
	value, err := strconv.ParseFloat(text, 64)
	if err != nil && !errors.Is(err, strconv.ErrRange) {
		return nil, importJSONDiagnostic("Expecting value", p.text, start)
	}
	return value, nil
}

func jsonNumber(text []rune) json.Number { return json.Number(string(text)) }

func importJSONHexDigit(r rune) bool {
	return r >= '0' && r <= '9' || r >= 'a' && r <= 'f' || r >= 'A' && r <= 'F'
}

// hex4 reads four hexadecimal digits at at (py_scanstring's _decode_uXXXX).
func (p *importJSONParser) hex4(at int) (rune, bool) {
	if at+4 > len(p.text) {
		return 0, false
	}
	value := 0
	for i := 0; i < 4; i++ {
		r := p.text[at+i]
		if !importJSONHexDigit(r) {
			return 0, false
		}
		digit := int(r - '0')
		if r >= 'a' {
			digit = int(r-'a') + 10
		} else if r >= 'A' {
			digit = int(r-'A') + 10
		}
		value = value<<4 | digit
	}
	return rune(value), true
}

// textValue is py_scanstring with strict=True after the opening quote.
func (p *importJSONParser) textValue() (any, error) {
	start := p.pos - 1
	out := make([]rune, 0, 16)
	for {
		if p.pos >= len(p.text) {
			return nil, importJSONDiagnostic("Unterminated string starting at", p.text, start)
		}
		c := p.text[p.pos]
		if c == '"' {
			p.pos++
			return pystr.String(out), nil
		}
		if c < 0x20 {
			return nil, importJSONDiagnostic("Invalid control character at", p.text, p.pos)
		}
		if c != '\\' {
			out = append(out, c)
			p.pos++
			continue
		}
		escape := p.pos
		p.pos++
		if p.pos >= len(p.text) {
			return nil, importJSONDiagnostic("Unterminated string starting at", p.text, start)
		}
		switch esc := p.text[p.pos]; esc {
		case '"', '\\', '/':
			out = append(out, esc)
			p.pos++
		case 'b':
			out = append(out, '\b')
			p.pos++
		case 'f':
			out = append(out, '\f')
			p.pos++
		case 'n':
			out = append(out, '\n')
			p.pos++
		case 'r':
			out = append(out, '\r')
			p.pos++
		case 't':
			out = append(out, '\t')
			p.pos++
		case 'u':
			unicode := p.pos
			r, ok := p.hex4(p.pos + 1)
			if !ok {
				return nil, importJSONDiagnostic(`Invalid \uXXXX escape`, p.text, unicode)
			}
			p.pos += 5
			if r >= 0xD800 && r <= 0xDBFF && p.pos+6 <= len(p.text) && p.text[p.pos] == '\\' && p.text[p.pos+1] == 'u' {
				if low, ok := p.hex4(p.pos + 2); ok && low >= 0xDC00 && low <= 0xDFFF {
					r = 0x10000 + (r-0xD800)<<10 + (low - 0xDC00)
					p.pos += 6
				}
			}
			out = append(out, r)
		default:
			return nil, importJSONDiagnostic(`Invalid \escape`, p.text, escape)
		}
	}
}

// object is one Python dict: a duplicate key keeps its first position and its
// last value.
func (p *importJSONParser) object(depth int) (any, error) {
	object := domain.RequestObject{}
	positions := map[string]int{}
	p.space()
	if p.take('}') {
		return object, nil
	}
	for {
		if p.pos >= len(p.text) || p.text[p.pos] != '"' {
			return nil, importJSONDiagnostic("Expecting property name enclosed in double quotes", p.text, p.pos)
		}
		p.pos++
		key, err := p.textValue()
		if err != nil {
			return nil, err
		}
		name := key.(string)
		p.space()
		if !p.take(':') {
			return nil, importJSONDiagnostic("Expecting ':' delimiter", p.text, p.pos)
		}
		value, err := p.value(depth + 1)
		if err != nil {
			return nil, err
		}
		if at, exists := positions[name]; exists {
			object[at].Value = value
		} else {
			positions[name] = len(object)
			object = append(object, domain.RequestField{Name: name, Value: value})
		}
		p.space()
		if p.take('}') {
			return object, nil
		}
		comma := p.pos
		if !p.take(',') {
			return nil, importJSONDiagnostic("Expecting ',' delimiter", p.text, p.pos)
		}
		p.space()
		if p.pos < len(p.text) && p.text[p.pos] == '}' {
			return nil, importJSONDiagnostic("Illegal trailing comma before end of object", p.text, comma)
		}
	}
}

func (p *importJSONParser) array(depth int) (any, error) {
	array := []any{}
	p.space()
	if p.take(']') {
		return array, nil
	}
	for {
		value, err := p.value(depth + 1)
		if err != nil {
			return nil, err
		}
		array = append(array, value)
		p.space()
		if p.take(']') {
			return array, nil
		}
		comma := p.pos
		if !p.take(',') {
			return nil, importJSONDiagnostic("Expecting ',' delimiter", p.text, p.pos)
		}
		p.space()
		if p.pos < len(p.text) && p.text[p.pos] == ']' {
			return nil, importJSONDiagnostic("Illegal trailing comma before end of array", p.text, comma)
		}
	}
}

// importJSONReader is the byte stream iter_json_array reads: the bytes
// archive.read returns here, a zipfile member later.
type importJSONReader interface{ Read(n int) ([]byte, error) }

// importBytesReader serves an already read member in chunk-size pieces.
type importBytesReader struct {
	data []byte
	at   int
}

func (r *importBytesReader) Read(n int) ([]byte, error) {
	if r.at >= len(r.data) {
		return nil, nil
	}
	end := r.at + n
	if end > len(r.data) {
		end = len(r.data)
	}
	chunk := r.data[r.at:end]
	r.at = end
	return chunk, nil
}

// importTextDecoder is codecs.getincrementaldecoder(detect_encoding(...))
// (errors='surrogatepass'). The BOM the detected encoding names is dropped
// from the first bytes and every later chunk runs the plain codec.
type importTextDecoder struct {
	codec   string
	skip    int
	pending []byte
}

func newImportTextDecoder(encoding string, first []byte) *importTextDecoder {
	decoder := &importTextDecoder{codec: encoding}
	switch encoding {
	case "utf-16":
		// detect_encoding names utf-16 only when the BOM starts the data.
		if len(first) >= 2 && first[0] == 0xFF {
			decoder.codec = "utf-16-le"
		} else {
			decoder.codec = "utf-16-be"
		}
		decoder.skip = 2
	case "utf-32":
		if len(first) >= 4 && first[0] == 0xFF {
			decoder.codec = "utf-32-le"
		} else {
			decoder.codec = "utf-32-be"
		}
		decoder.skip = 4
	case "utf-8-sig":
		decoder.codec, decoder.skip = "utf-8", 3
	}
	return decoder
}

// importIncompleteTail is the length of the trailing bytes a non-final
// decode leaves for the next one: a partial character, or (utf-16) a trailing
// high surrogate waiting for its low surrogate.
func importIncompleteTail(codec string, data []byte) int {
	switch codec {
	case "utf-16-le", "utf-16-be":
		rem := len(data) % 2
		units := len(data) - rem
		tail := rem
		if units >= 2 {
			var last rune
			if codec == "utf-16-le" {
				last = rune(data[units-2]) | rune(data[units-1])<<8
			} else {
				last = rune(data[units-2])<<8 | rune(data[units-1])
			}
			if last >= 0xD800 && last <= 0xDBFF {
				tail += 2
			}
		}
		return tail
	case "utf-32-le", "utf-32-be":
		return len(data) % 4
	}
	return importUTF8IncompleteTail(data)
}

// importUTF8IncompleteTail is the length of a trailing sequence a valid
// UTF-8 stream could still complete; an already invalid sequence is decoded
// now (and fails).
func importUTF8IncompleteTail(data []byte) int {
	n := len(data)
	for back := 1; back <= 4 && back <= n; back++ {
		b := data[n-back]
		if b >= 0x80 && b < 0xC0 {
			continue
		}
		need := 0
		switch {
		case b >= 0xC2 && b <= 0xDF:
			need = 2
		case b >= 0xE0 && b <= 0xEF:
			need = 3
		case b >= 0xF0 && b <= 0xF4:
			need = 4
		default:
			return 0
		}
		if back >= need {
			return 0
		}
		if !importUTF8LegalPrefix(data[n-back:]) {
			return 0
		}
		return back
	}
	return 0
}

// importUTF8LegalPrefix checks the second byte's range for the lead byte
// (Unicode Table 3-7); every later byte is already a continuation byte.
func importUTF8LegalPrefix(part []byte) bool {
	if len(part) < 2 {
		return true
	}
	lead, second := part[0], part[1]
	switch {
	case lead == 0xE0:
		return second >= 0xA0 && second <= 0xBF
	case lead == 0xED:
		return second >= 0x80 && second <= 0x9F
	case lead == 0xF0:
		return second >= 0x90 && second <= 0xBF
	case lead == 0xF4:
		return second >= 0x80 && second <= 0x8F
	}
	return second >= 0x80 && second <= 0xBF
}

func (d *importTextDecoder) decode(chunk []byte, final bool) (string, error) {
	if d.skip > 0 {
		if len(chunk) < d.skip {
			d.pending = append(d.pending, chunk...)
			return "", nil
		}
		chunk = chunk[d.skip:]
		d.skip = 0
	}
	data := append(d.pending, chunk...)
	d.pending = nil
	if !final {
		if tail := importIncompleteTail(d.codec, data); tail > 0 {
			d.pending = append([]byte(nil), data[len(data)-tail:]...)
			data = data[:len(data)-tail]
		}
	}
	out, err, ok := pycodecs.Decode(d.codec, data, pycodecs.SurrogatePass)
	if !ok {
		return "", errors.New("json: unported codec " + d.codec)
	}
	if err != nil {
		return "", err
	}
	return pystr.String(out), nil
}

// importChunkedText is json_stream._ChunkedTextReader.
type importChunkedText struct {
	stream    importJSONReader
	chunkSize int
	decoder   *importTextDecoder
	pending   []byte
	eof       bool
}

func (r *importChunkedText) read() (string, error) {
	if r.eof {
		return "", nil
	}
	raw, err := r.stream.Read(r.chunkSize)
	if err != nil {
		return "", err
	}
	if len(raw) == 0 {
		r.eof = true
		if r.decoder == nil {
			if len(r.pending) == 0 {
				return "", nil
			}
			return r.startDecoder(true)
		}
		return r.decoder.decode(nil, true)
	}
	if r.decoder == nil {
		r.pending = append(r.pending, raw...)
		if len(r.pending) < 4 {
			return "", nil
		}
		return r.startDecoder(false)
	}
	return r.decoder.decode(raw, false)
}

func (r *importChunkedText) startDecoder(final bool) (string, error) {
	first := r.pending
	r.pending = nil
	r.decoder = newImportTextDecoder(importJSONEncoding(first), first)
	return r.decoder.decode(first, final)
}

// importJSONBuffer is json_stream._JsonBuffer: a sliding window over the
// decoded stream with an index cursor, so a JSONDecodeError's positions are
// relative to the remaining window, as raw_decode sees it.
type importJSONBuffer struct {
	reader *importChunkedText
	data   []rune
	pos    int
}

func (b *importJSONBuffer) fill() (bool, error) {
	if b.pos > 0 {
		b.data = b.data[b.pos:]
		b.pos = 0
	}
	for {
		chunk, err := b.reader.read()
		if err != nil {
			return false, err
		}
		if chunk != "" {
			b.data = append(b.data, pystr.Runes(chunk)...)
			return true, nil
		}
		if b.reader.eof {
			return false, nil
		}
	}
}

func (b *importJSONBuffer) peek() (rune, bool, error) {
	for {
		for b.pos < len(b.data) {
			switch c := b.data[b.pos]; c {
			case ' ', '\t', '\r', '\n':
				b.pos++
			default:
				return c, true, nil
			}
		}
		filled, err := b.fill()
		if err != nil {
			return 0, false, err
		}
		if !filled {
			return 0, false, nil
		}
	}
}

func (b *importJSONBuffer) expectEnd() error {
	for {
		for b.pos < len(b.data) {
			switch b.data[b.pos] {
			case ' ', '\t', '\r', '\n':
				b.pos++
			default:
				return errors.New("Extra data after JSON array")
			}
		}
		filled, err := b.fill()
		if err != nil {
			return err
		}
		if !filled {
			return nil
		}
	}
}

// rawDecode is decoder.raw_decode(buffer.data, buffer.pos); deep reports a
// value that reached the recursion band (the caller decides its outcome once
// no more data can change it).
func (b *importJSONBuffer) rawDecode() (value any, end int, deep bool, err error) {
	p := &importJSONParser{text: b.data, pos: b.pos}
	value, err = p.value(0)
	if err != nil {
		return nil, 0, p.deep, err
	}
	return value, p.pos, p.deep, nil
}

func importJSONValueTerminator(c rune) bool {
	switch c {
	case ' ', '\t', '\r', '\n', ',', ']':
		return true
	}
	return false
}

// importIterJSONArray is json_stream.iter_json_array: the items of a
// top-level JSON array one at a time, with the same ValueError texts for the
// array's own grammar and raw_decode's JSONDecodeError for an item. The
// callback is the loop body, so an error it returns stops the iteration.
func importIterJSONArray(reader importJSONReader, chunkSize int, yield func(any) error) error {
	buffer := &importJSONBuffer{reader: &importChunkedText{stream: reader, chunkSize: chunkSize}}
	// first -> '['; value_or_end -> first item or ']'; separator -> ',' or ']';
	// value -> item required after a comma.
	state := "first"
	for {
		char, ok, err := buffer.peek()
		if err != nil {
			return err
		}
		if !ok {
			if state == "first" {
				return errors.New("Expected a JSON array")
			}
			return errors.New("Unterminated JSON array")
		}
		if state == "first" {
			if char != '[' {
				return errors.New("Expected a JSON array")
			}
			state = "value_or_end"
			buffer.pos++
			continue
		}
		if char == ']' {
			if state == "value" {
				return errors.New("Expecting value")
			}
			buffer.pos++
			return buffer.expectEnd()
		}
		if char == ',' {
			if state != "separator" {
				return errors.New("Expecting value")
			}
			state = "value"
			buffer.pos++
			continue
		}
		if state == "separator" {
			return errors.New("Expecting ',' delimiter")
		}
		var item any
		for {
			value, end, deep, err := buffer.rawDecode()
			var recursion *importJSONRecursionError
			if errors.As(err, &recursion) || errors.Is(err, dto.ErrJSONRecursionBand) {
				// RecursionError is no ValueError: the loop does not retry it.
				return err
			}
			if err != nil {
				filled, fillErr := buffer.fill()
				if fillErr != nil {
					return fillErr
				}
				if !filled {
					if deep {
						return dto.ErrJSONRecursionBand // see bandOutcome
					}
					return err
				}
				continue
			}
			if deep {
				return dto.ErrJSONRecursionBand // parsed here, a RecursionError on a deeper stack
			}
			// A chunk boundary can truncate a number or literal into a shorter
			// valid value ("1.5e3" as 1): accept it only after a terminator or
			// the end of the stream proves the token complete.
			if end < len(buffer.data) {
				if importJSONValueTerminator(buffer.data[end]) {
					buffer.pos, item = end, value
					break
				}
			} else if buffer.reader.eof {
				buffer.pos, item = end, value
				break
			}
			if buffer.reader.eof {
				return errors.New("Expecting ',' delimiter")
			}
			if _, fillErr := buffer.fill(); fillErr != nil {
				return fillErr
			}
		}
		if err := yield(item); err != nil {
			return err
		}
		state = "separator"
	}
}

// importJSONAttributeError is the AttributeError an operation on the wrong
// JSON type raises ("'str' object has no attribute 'get'").
type importJSONAttributeError struct{ kind, attribute string }

func (e *importJSONAttributeError) Error() string {
	return "'" + e.kind + "' object has no attribute '" + e.attribute + "'"
}

// importJSONIterableError is TypeError for a non-iterable in a for loop.
type importJSONIterableError struct{ kind string }

func (e *importJSONIterableError) Error() string { return "'" + e.kind + "' object is not iterable" }

// importJSONContainsError is TypeError for `x in value` on a non-iterable.
type importJSONContainsError struct{ kind string }

func (e *importJSONContainsError) Error() string {
	return "argument of type '" + e.kind + "' is not iterable"
}

// importJSONTypeName is type(value).__name__ for a decoded JSON value.
func importJSONTypeName(value any) string {
	switch v := value.(type) {
	case nil:
		return "NoneType"
	case string:
		return "str"
	case bool:
		return "bool"
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return "float"
		}
		return "int"
	case float64:
		return "float"
	case domain.RequestObject:
		return "dict"
	case []any:
		return "list"
	}
	return "object"
}

// importJSONTruthy is bool(value) for a decoded JSON value.
func importJSONTruthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case string:
		return v != ""
	case bool:
		return v
	case json.Number:
		f, err := strconv.ParseFloat(string(v), 64)
		return err != nil || f != 0
	case float64:
		return v != 0
	case domain.RequestObject:
		return len(v) > 0
	case []any:
		return len(v) > 0
	}
	return true
}

// importJSONStr is str(value) for a decoded JSON value.
func importJSONStr(value any) string { return dto.PythonStr(value) }

// importJSONIterate is `for item in value` for a decoded JSON value: a dict
// yields its keys, a str its code points, a list its items.
func importJSONIterate(value any) ([]any, error) {
	switch v := value.(type) {
	case domain.RequestObject:
		keys := make([]any, len(v))
		for i, field := range v {
			keys[i] = field.Name
		}
		return keys, nil
	case []any:
		return v, nil
	case string:
		runes := pystr.Runes(v)
		items := make([]any, len(runes))
		for i, r := range runes {
			items[i] = pystr.String([]rune{r})
		}
		return items, nil
	}
	return nil, &importJSONIterableError{kind: importJSONTypeName(value)}
}

// importJSONContains is `needle in value` for a decoded JSON value: substring
// in a str, a key in a dict, an item in a list.
func importJSONContains(value any, needle string) (bool, error) {
	switch v := value.(type) {
	case string:
		return strings.Contains(v, needle), nil
	case domain.RequestObject:
		for _, field := range v {
			if field.Name == needle {
				return true, nil
			}
		}
		return false, nil
	case []any:
		for _, item := range v {
			if text, ok := item.(string); ok && text == needle {
				return true, nil
			}
		}
		return false, nil
	}
	return false, &importJSONContainsError{kind: importJSONTypeName(value)}
}

// importJSONNameListHas is `value in namelist`: a zipfile namelist holds only
// strings, so any other value compares unequal.
func importJSONNameListHas(names []string, value any) bool {
	text, ok := value.(string)
	if !ok {
		return false
	}
	for _, name := range names {
		if name == text {
			return true
		}
	}
	return false
}
