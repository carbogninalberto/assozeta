// The stateful ISO-2022 codecs and hz: CPython's _multibytecodec stateful
// decoders from Modules/cjkcodecs/_codecs_iso2022.c and _codecs_cn.c.
//
// An ISO-2022 stream designates a charset into one of four graphic sets (G0,
// G1, G2) with an escape sequence, switches between G0 and G1 with SI and SO,
// and decodes each graphic byte (or byte pair) through the designated charset.
// The line feed resets the shift, and an ESC that is not an ISO-2022 escape
// starts CPython's "ESC throughout" mode, which copies ISO-8859-1 bytes until
// the next upper-case letter or '@'. hz is a smaller state machine of its own:
// '~{' enters GB mode, '~}' leaves it, '~~' is a tilde and "~\n" a line
// continuation.
//
// The charsets are generated from CPython by testdata/gen_iso2022.py: every
// charset is derived by decoding each byte (pair) through a codec that
// designates it, so the composed decoders (JIS X 0213's combining pairs and
// its emulation of the 2000 standard, JIS X 0208's F/W reverse solidus,
// ISO-8859-7's G2 table) are carried as CPython applies them.
package pycodecs

import (
	"bytes"
	"compress/gzip"
	_ "embed"
	"encoding/binary"
	"io"
	"sync"
)

// iso2022Codecs are the stateful ISO-2022 codecs, by codecs.lookup(name).name.
var iso2022Codecs = []string{
	"iso2022_jp", "iso2022_jp_1", "iso2022_jp_2", "iso2022_jp_2004",
	"iso2022_jp_3", "iso2022_jp_ext", "iso2022_kr",
}

// The control bytes of _codecs_iso2022.c.
const (
	iso2022Esc = 0x1B
	iso2022SO  = 0x0E
	iso2022SI  = 0x0F
	iso2022LF  = 0x0A

	// iso2022ASCII marks the ASCII graphic set ('B', CHARSET_ASCII).
	iso2022ASCII = 'B'

	// iso2022MaxEscSeqLen bounds the escape sequence scan; a sequence that is
	// not terminated within it is an illegal one-byte span (MAX_ESCSEQLEN).
	iso2022MaxEscSeqLen = 16

	// iso2022PairBit marks a table entry that holds an index into the
	// charset's pair table instead of a code point.
	iso2022PairBit = 0x80000000
)

// iso2022Config flags (struct iso2022_config.flags). Without noShift SI and SO
// are plain characters (all the iso2022_jp variants set it; iso2022_kr does
// not).
const (
	iso2022NoShift        = 0x01
	iso2022UseG2          = 0x02
	iso2022UseJISX0208Ext = 0x04
)

// iso2022InternalError is the RuntimeError("internal codec error") CPython
// raises when a G2 designation names a charset its processg2 does not
// implement (iso2022_jp_2's "ESC . J", "ESC . <a DBCS mark>", ...). It is
// raised whatever the error handler.
type iso2022InternalError struct{}

func (iso2022InternalError) Error() string { return "internal codec error" }

// iso2022_tables.bin.gz is written by testdata/gen_iso2022.py.
//
//go:embed iso2022_tables.bin.gz
var iso2022Blob []byte

// iso2022Charset is one designation: its width and its decode tables.
type iso2022Charset struct {
	name string
	// width is 1 or 2 graphic bytes per character; CPython's decoders look at
	// exactly that many bytes (a width 1 decoder ignores the second).
	width int
	// grid maps a graphic byte (pair) to a table entry: (first-0x20)*256 +
	// second for width 2, first-0x20 for width 1. Zero means unmappable, a
	// value with iso2022PairBit set indexes pairs, otherwise the value is the
	// code point. G0/G1 decode through it (the ISO-8859 G2 charsets have no
	// grid: CPython's decoder for them only fails, so any byte is an illegal
	// one-byte span).
	grid []uint32
	// g2 is the table the ESC N single shift uses (256 entries, same
	// encoding), or nil when the charset cannot be used in G2.
	g2    []uint32
	pairs [][2]rune
}

// iso2022Codec is one codec's configuration: its designations and flags.
type iso2022Codec struct {
	name    string
	flags   byte
	charset map[byte]*iso2022Charset
}

var (
	iso2022Once    sync.Once
	iso2022ByName  map[string]*iso2022Codec
	iso2022ByChars map[string]*iso2022Charset
)

// iso2022TableOf parses the embedded blob once, on the first decode.
func iso2022TableOf(name string) *iso2022Codec {
	iso2022Once.Do(loadISO2022Tables)
	codec, ok := iso2022ByName[name]
	if !ok {
		panic("pycodecs: no ISO-2022 configuration for " + name)
	}
	return codec
}

// iso2022CharsetOf returns a charset by its generated name.
func iso2022CharsetOf(name string) *iso2022Charset {
	iso2022Once.Do(loadISO2022Tables)
	charset, ok := iso2022ByChars[name]
	if !ok {
		panic("pycodecs: no ISO-2022 charset " + name)
	}
	return charset
}

// loadISO2022Tables decodes iso2022_tables.bin.gz. The format is:
//
//	gzip of "PYISO2", uvarint charset count, then per charset:
//	  uvarint name length, name
//	  byte width (1 or 2)
//	  byte kind: 0 the charset has a grid, 1 it is always unmappable
//	  grid: 96*256 entries (width 2) or 96 (width 1), each
//	    uvarint: 0 unmappable, 1 a code point pair (then two uvarints), else
//	    code point + 2
//	  byte g2 kind: 0 no G2 table, 1 ISO-8859-1, 2 ISO-8859-7
//	  g2: 256 entries in the grid's encoding when the kind is not 0
//	then uvarint codec count, then per codec, sorted by name:
//	  uvarint name length, name
//	  byte flags (iso2022NoShift, iso2022UseG2, iso2022UseJISX0208Ext)
//	  uvarint designation count, then per designation:
//	    byte mark, uvarint charset index
//
// A malformed blob can only come from a bad build, so parsing panics.
func loadISO2022Tables() {
	compressed, err := gzip.NewReader(bytes.NewReader(iso2022Blob))
	if err != nil {
		panic("pycodecs: iso2022_tables.bin.gz: " + err.Error())
	}
	raw, err := io.ReadAll(compressed)
	if err != nil {
		panic("pycodecs: iso2022_tables.bin.gz: " + err.Error())
	}
	if len(raw) < 6 || string(raw[:6]) != "PYISO2" {
		panic("pycodecs: iso2022_tables.bin.gz: bad magic")
	}
	reader := &iso2022Reader{data: raw, pos: 6}
	charsets := make([]*iso2022Charset, reader.uvarint())
	byName := make(map[string]*iso2022Charset, len(charsets))
	for i := range charsets {
		charset := &iso2022Charset{name: string(reader.take(int(reader.uvarint())))}
		charset.width = int(reader.byte())
		if charset.width != 1 && charset.width != 2 {
			panic("pycodecs: iso2022_tables.bin.gz: bad charset width")
		}
		switch reader.byte() {
		case 0:
			count := 96
			if charset.width == 2 {
				count = 96 * 256
			}
			charset.grid = reader.table(count, charset)
		case 1:
		default:
			panic("pycodecs: iso2022_tables.bin.gz: bad charset kind")
		}
		switch reader.byte() {
		case 0:
		case 1, 2:
			charset.g2 = reader.table(256, charset)
		default:
			panic("pycodecs: iso2022_tables.bin.gz: bad G2 kind")
		}
		charsets[i] = charset
		byName[charset.name] = charset
	}
	codecCount := int(reader.uvarint())
	codecs := make(map[string]*iso2022Codec, codecCount)
	for ; codecCount > 0; codecCount-- {
		codec := &iso2022Codec{name: string(reader.take(int(reader.uvarint())))}
		codec.flags = reader.byte()
		designations := int(reader.uvarint())
		codec.charset = make(map[byte]*iso2022Charset, designations)
		for i := 0; i < designations; i++ {
			mark := reader.byte()
			index := int(reader.uvarint())
			if index >= len(charsets) {
				panic("pycodecs: iso2022_tables.bin.gz: charset index out of range")
			}
			if _, taken := codec.charset[mark]; !taken {
				codec.charset[mark] = charsets[index]
			}
		}
		codecs[codec.name] = codec
	}
	if reader.pos != len(reader.data) {
		panic("pycodecs: iso2022_tables.bin.gz: trailing bytes")
	}
	iso2022ByName = codecs
	iso2022ByChars = byName
}

// iso2022Reader reads the little integers of the table blob.
type iso2022Reader struct {
	data []byte
	pos  int
}

func (r *iso2022Reader) byte() byte {
	if r.pos >= len(r.data) {
		panic("pycodecs: iso2022_tables.bin.gz: truncated")
	}
	value := r.data[r.pos]
	r.pos++
	return value
}

func (r *iso2022Reader) take(n int) []byte {
	if n < 0 || r.pos+n > len(r.data) {
		panic("pycodecs: iso2022_tables.bin.gz: truncated")
	}
	value := r.data[r.pos : r.pos+n]
	r.pos += n
	return value
}

func (r *iso2022Reader) uvarint() int {
	value, size := binary.Uvarint(r.data[r.pos:])
	if size <= 0 {
		panic("pycodecs: iso2022_tables.bin.gz: bad varint")
	}
	r.pos += size
	return int(value)
}

// table reads count table entries into charset, whose pair table it fills.
func (r *iso2022Reader) table(count int, charset *iso2022Charset) []uint32 {
	entries := make([]uint32, count)
	for i := range entries {
		switch value := r.uvarint(); {
		case value == 0:
		case value == 1:
			pair := [2]rune{rune(r.uvarint()), rune(r.uvarint())}
			entries[i] = iso2022PairBit | uint32(len(charset.pairs))
			charset.pairs = append(charset.pairs, pair)
		default:
			entries[i] = uint32(value - 1)
		}
	}
	return entries
}

// lookup decodes a table entry: one code point, a pair, or nothing.
func (c *iso2022Charset) lookup(entry uint32) (first, second rune, size int, ok bool) {
	if entry == 0 {
		return 0, 0, 0, false
	}
	if entry&iso2022PairBit != 0 {
		pair := c.pairs[entry&^iso2022PairBit]
		return pair[0], pair[1], 2, true
	}
	return rune(entry - 1), 0, 1, true
}

// decodeGraphic decodes the graphic byte(s) at b1 and b2 through the charset.
func (c *iso2022Charset) decodeGraphic(b1, b2 byte) (first, second rune, size int, ok bool) {
	if c.grid == nil || b1 < 0x20 || b1 > 0x7F {
		return 0, 0, 0, false
	}
	index := int(b1) - 0x20
	if c.width == 2 {
		index = index*256 + int(b2)
	}
	return c.lookup(c.grid[index])
}

func init() {
	register("hz", decodeHZ)
	for _, name := range iso2022Codecs {
		register(name, func(data []byte, sink *Sink) error {
			return iso2022TableOf(name).decode(data, sink)
		})
	}
}

// iso2022State is MultibyteCodec_State.c for _codecs_iso2022.c: the charset
// designated into G0, G1 and G2, and the shifted and ESC-throughout flags.
type iso2022State struct {
	g0, g1, g2 byte
	shifted    bool
	escThrough bool
}

// isISO2022EscEnd is IS_ESCEND: an escape sequence ends at an upper-case
// letter or at '@'.
func isISO2022EscEnd(c byte) bool {
	return (c >= 'A' && c <= 'Z') || c == '@'
}

// isISO2022EscHeader is IS_ISO2022ESC: the second byte of the escape
// sequences the CJK codecs understand.
func isISO2022EscHeader(c byte) bool {
	switch c {
	case '(', ')', '$', '.', '&':
		return true
	}
	return false
}

// decode walks the whole input, keeping the state across handled errors.
func (c *iso2022Codec) decode(data []byte, sink *Sink) error {
	state := iso2022State{g0: iso2022ASCII, g1: iso2022ASCII, g2: iso2022ASCII}
	for pos := 0; pos < len(data); {
		next, err := c.step(data, pos, &state, sink)
		if err != nil {
			return err
		}
		pos = next
	}
	return nil
}

// step decodes the character at pos (one byte of the ISO-2022 stream) and
// returns the position after it (Sink.Fail's when a span was handled).
func (c *iso2022Codec) step(data []byte, pos int, state *iso2022State, sink *Sink) (int, error) {
	b := data[pos]

	if state.escThrough {
		// ESC throughout mode: copy the byte as ISO-8859-1 until an escape
		// sequence end byte.
		sink.Out = append(sink.Out, rune(b))
		if isISO2022EscEnd(b) {
			state.escThrough = false
		}
		return pos + 1, nil
	}

	switch b {
	case iso2022Esc:
		if pos+1 >= len(data) {
			return sink.Fail(pos, len(data), cjkIncomplete)
		}
		if isISO2022EscHeader(data[pos+1]) {
			return c.stepEscape(data, pos, state, sink)
		}
		if c.flags&iso2022UseG2 != 0 && data[pos+1] == 'N' {
			if pos+2 >= len(data) {
				return sink.Fail(pos, len(data), cjkIncomplete)
			}
			return c.stepSingleShift(data, pos, state, sink)
		}
		sink.Out = append(sink.Out, rune(iso2022Esc))
		state.escThrough = true
		return pos + 1, nil
	case iso2022SI:
		if c.flags&iso2022NoShift != 0 {
			break // a plain C0 character
		}
		state.shifted = false
		return pos + 1, nil
	case iso2022SO:
		if c.flags&iso2022NoShift != 0 {
			break // a plain C0 character
		}
		state.shifted = true
		return pos + 1, nil
	case iso2022LF:
		// A line feed resets the shift and is written as itself.
		state.shifted = false
		sink.Out = append(sink.Out, rune(iso2022LF))
		return pos + 1, nil
	}

	if b < 0x20 {
		// Any other C0 character is copied as itself.
		sink.Out = append(sink.Out, rune(b))
		return pos + 1, nil
	}
	if b >= 0x80 {
		return sink.Fail(pos, pos+1, cjkIllegal)
	}

	mark := state.g0
	if state.shifted {
		mark = state.g1
	}
	if mark == iso2022ASCII {
		sink.Out = append(sink.Out, rune(b))
		return pos + 1, nil
	}
	charset := c.charset[mark]
	if charset == nil {
		// Unreachable through the escape validation; CPython reports one byte.
		return sink.Fail(pos, pos+1, cjkIllegal)
	}
	if charset.width == 1 {
		first, _, _, ok := charset.decodeGraphic(b, 0)
		if !ok {
			return sink.Fail(pos, pos+1, cjkIllegal)
		}
		sink.Out = append(sink.Out, first)
		return pos + 1, nil
	}
	if pos+1 >= len(data) {
		return sink.Fail(pos, len(data), cjkIncomplete)
	}
	first, second, size, ok := charset.decodeGraphic(b, data[pos+1])
	if !ok {
		return sink.Fail(pos, pos+2, cjkIllegal)
	}
	sink.Out = append(sink.Out, first)
	if size == 2 {
		sink.Out = append(sink.Out, second)
	}
	return pos + 2, nil
}

// stepEscape handles an escape sequence whose second byte is an ISO-2022
// escape header (iso2022processesc plus the decoder's ESC case).
func (c *iso2022Codec) stepEscape(data []byte, pos int, state *iso2022State, sink *Sink) (int, error) {
	escLen := 0
	for i := 1; i < iso2022MaxEscSeqLen; i++ {
		if pos+i >= len(data) {
			return sink.Fail(pos, len(data), cjkIncomplete)
		}
		b := data[pos+i]
		if isISO2022EscEnd(b) {
			escLen = i + 1
			break
		}
		if c.flags&iso2022UseJISX0208Ext != 0 && pos+i+1 < len(data) &&
			b == '&' && data[pos+i+1] == '@' {
			// The JIS X 0208 extension prefix "ESC & @ ESC $ B".
			i += 2
		}
	}

	var mark byte
	designation := 0
	switch escLen {
	case 0:
		// No escape sequence end within MAX_ESCSEQLEN: one illegal byte.
		return sink.Fail(pos, pos+1, cjkIllegal)
	case 3:
		if data[pos+1] == '$' {
			mark = data[pos+2] | 0x80
		} else {
			mark = data[pos+2]
			switch data[pos+1] {
			case '(':
				// G0
			case ')':
				designation = 1
			case '.':
				if c.flags&iso2022UseG2 == 0 {
					return sink.Fail(pos, pos+3, cjkIllegal)
				}
				designation = 2
			default:
				return sink.Fail(pos, pos+3, cjkIllegal)
			}
		}
	case 4:
		if data[pos+1] != '$' {
			return sink.Fail(pos, pos+4, cjkIllegal)
		}
		mark = data[pos+3] | 0x80
		switch data[pos+2] {
		case '(':
			// G0
		case ')':
			designation = 1
		default:
			return sink.Fail(pos, pos+4, cjkIllegal)
		}
	case 6:
		if c.flags&iso2022UseJISX0208Ext != 0 && data[pos+3] == iso2022Esc &&
			data[pos+4] == '$' && data[pos+5] == 'B' {
			mark = 'B' | 0x80
		} else {
			return sink.Fail(pos, pos+6, cjkIllegal)
		}
	default:
		return sink.Fail(pos, pos+escLen, cjkIllegal)
	}

	// The charset must be designated by this codec's config; ASCII always is.
	if mark != iso2022ASCII && c.charset[mark] == nil {
		return sink.Fail(pos, pos+escLen, cjkIllegal)
	}
	switch designation {
	case 0:
		state.g0 = mark
	case 1:
		state.g1 = mark
	default:
		state.g2 = mark
	}
	return pos + escLen, nil
}

// stepSingleShift handles "ESC N" (SS2) plus one byte (iso2022processg2).
func (c *iso2022Codec) stepSingleShift(data []byte, pos int, state *iso2022State, sink *Sink) (int, error) {
	b := data[pos+2]
	if state.g2 == iso2022ASCII {
		if b >= 0x80 {
			return sink.Fail(pos, pos+3, cjkIllegal)
		}
		sink.Out = append(sink.Out, rune(b))
		return pos + 3, nil
	}
	charset := c.charset[state.g2]
	if charset == nil || charset.g2 == nil {
		return 0, iso2022InternalError{}
	}
	first, second, size, ok := charset.lookup(charset.g2[b])
	if !ok {
		return sink.Fail(pos, pos+3, cjkIllegal)
	}
	sink.Out = append(sink.Out, first)
	if size == 2 {
		sink.Out = append(sink.Out, second)
	}
	return pos + 3, nil
}

// hz is the HZ-GB-2312 decoder of Modules/cjkcodecs/_codecs_cn.c: ASCII until
// '~{', GB 2312 byte pairs until '~}', with '~~' for a tilde and "~\n" as a
// line continuation (both only in ASCII mode).
func decodeHZ(data []byte, sink *Sink) error {
	gb := iso2022CharsetOf("gb2312")
	inGB := false
	for pos := 0; pos < len(data); {
		next, err := hzStep(data, pos, &inGB, gb, sink)
		if err != nil {
			return err
		}
		pos = next
	}
	return nil
}

func hzStep(data []byte, pos int, inGB *bool, gb *iso2022Charset, sink *Sink) (int, error) {
	b := data[pos]
	if b == '~' {
		if pos+1 >= len(data) {
			return sink.Fail(pos, len(data), cjkIncomplete)
		}
		switch c2 := data[pos+1]; {
		case c2 == '~' && !*inGB:
			sink.Out = append(sink.Out, '~')
		case c2 == '{' && !*inGB:
			*inGB = true
		case c2 == '\n' && !*inGB:
			// Line continuation: nothing is written.
		case c2 == '}' && *inGB:
			*inGB = false
		default:
			return sink.Fail(pos, pos+1, cjkIllegal)
		}
		return pos + 2, nil
	}
	if b >= 0x80 {
		return sink.Fail(pos, pos+1, cjkIllegal)
	}
	if !*inGB {
		sink.Out = append(sink.Out, rune(b))
		return pos + 1, nil
	}
	if pos+1 >= len(data) {
		return sink.Fail(pos, len(data), cjkIncomplete)
	}
	first, _, _, ok := gb.decodeGraphic(b, data[pos+1])
	if !ok {
		return sink.Fail(pos, pos+1, cjkIllegal)
	}
	sink.Out = append(sink.Out, first)
	return pos + 2, nil
}
