package pandascsv

import (
	"bytes"
	"unicode/utf8"
)

// The states of pandas' tokenizer.c that delimiter=';' with the default
// options can reach.
const (
	tokStartRecord = iota
	tokStartField
	tokInField
	tokInQuoted
	tokQuoteInQuoted
	tokEatCRNL
	tokEatCRNLNop
	tokWhitespaceLine
	tokFinished
)

// readBuffer is the C parser's chunksize: the size of one read() from the
// file object, in bytes for a raw handle and in characters for a decoded one.
const readBuffer = 262144

// tokenizer is tokenizer.c's parser_t: the token stream of the lines that are
// buffered and the state that survives between tokenize calls.
type tokenizer struct {
	source  []byte
	next    int  // offset of the next read() in source
	decoded bool // buffers hold readBuffer characters, not bytes

	data    []byte // the current buffer
	datapos int

	state     int
	lines     int // completed buffered lines
	fileLines int

	// Every word ends with a NUL in stream, as in C: a word is read up to
	// its first NUL, so a NUL inside a field cuts it short.
	stream     []byte
	pending    int // where the word being read starts in stream
	wordStarts []int
	lineStart  []int // index of each line's first word; one entry past lines
	lineFields []int

	// The parser's buffers only grow, in makeStreamSpace, and a word, a field
	// byte or a line past the capacity is the ParserError 'Buffer overflow
	// caught - possible malformed input file.'. streamLen is the C parser's
	// stream_p: the bytes in the stream from streamBase on, every word being
	// its bytes and a NUL.
	streamLen  int
	streamCap  int
	streamBase int
	wordsCap   int
	linesCap   int

	// blankStart is where the whitespace that opens the current line began in
	// the buffer (0 when it began in an earlier one); reread records that a
	// rewind went back to before it, so buffered lines were read again.
	blankStart int
	reread     bool
}

func newTokenizer(source []byte, decoded bool) *tokenizer {
	return &tokenizer{source: source, decoded: decoded, lineStart: []int{0}, lineFields: []int{0}}
}

// word is the C string of word index.
func (t *tokenizer) word(index int) []byte {
	start := t.wordStarts[index]
	return t.stream[start : start+bytes.IndexByte(t.stream[start:], 0)]
}

// field is COLITER_NEXT: the word of a buffered line's column, or "" past
// the line's last field.
func (t *tokenizer) field(line, column int) []byte {
	index := t.lineStart[line] + column
	if index >= t.lineStart[line+1] {
		return nil
	}
	return t.word(index)
}

// overflow is the ParserError a full buffer raises. After the tokenizer has
// read buffered lines again it is not known how many lines the C parser fits
// before its buffers are full (or whether the reader's row limit stops it
// first), so the outcome is left to Django.
func (t *tokenizer) overflow() error {
	if t.reread {
		return ErrBareCRBacktrack
	}
	return ErrParser
}

// growCap is make_stream_space's doubling: the smallest power of two above
// need, starting from the capacity the buffer has.
func growCap(need, capacity int) int {
	for capacity == 0 || need >= capacity {
		if capacity == 0 {
			capacity = 2
		} else {
			capacity *= 2
		}
	}
	return capacity
}

// makeStreamSpace is make_stream_space: room for nbytes more bytes of fields,
// and for as many more words and lines.
func (t *tokenizer) makeStreamSpace(nbytes int) {
	t.streamCap = growCap(t.streamLen+2*nbytes, t.streamCap)
	base := len(t.wordStarts)
	if len(t.wordStarts)+nbytes < t.wordsCap {
		base = t.wordsCap - nbytes - 1
	}
	t.wordsCap = growCap(base+nbytes, t.wordsCap)
	t.linesCap = growCap(t.lines+nbytes+1, t.linesCap)
}

// appendByte appends one byte of a field to the stream.
func (t *tokenizer) appendByte(c byte) error {
	if t.streamLen >= t.streamCap {
		return t.overflow()
	}
	t.stream = append(t.stream, c)
	t.streamLen++
	return nil
}

// endField is the end of a field: the word's NUL, the word and the line's
// field count.
func (t *tokenizer) endField() error {
	if len(t.wordStarts) >= t.wordsCap || t.streamLen >= t.streamCap {
		return t.overflow()
	}
	t.stream = append(t.stream, 0)
	t.streamLen++
	t.wordStarts = append(t.wordStarts, t.pending)
	t.lineFields[t.lines]++
	t.pending = len(t.stream)
	return nil
}

// endLine is end_line: a line after the first data line with more fields
// than the line before it is the ParserError; a shorter one is padded.
func (t *tokenizer) endLine() error {
	fields := t.lineFields[t.lines]
	expected := -1
	if t.lines > 0 {
		expected = t.lineFields[t.lines-1]
	}
	if t.lines > 1 && fields > expected {
		return ErrParser
	}
	if t.lines >= 1 && fields < expected {
		t.makeStreamSpace(expected - fields)
		for ; fields < expected; fields++ {
			if err := t.endField(); err != nil {
				return err
			}
		}
	}
	t.fileLines++
	t.lines++
	if t.lines >= t.linesCap {
		return t.overflow()
	}
	t.lineStart = append(t.lineStart, t.lineStart[t.lines-1]+fields)
	t.lineFields = append(t.lineFields, 0)
	return nil
}

// consume is parser_consume_rows: the first rows leave the buffer.
func (t *tokenizer) consume(rows int) {
	rows = min(rows, t.lines)
	if rows == 0 {
		return
	}
	words := t.lineStart[rows-1] + t.lineFields[rows-1]
	if words > 0 {
		end := len(t.stream)
		if words < len(t.wordStarts) {
			end = t.wordStarts[words]
		}
		t.streamLen -= end - t.streamBase
		t.streamBase = end
	}
	t.wordStarts = t.wordStarts[words:]
	t.lineFields = t.lineFields[rows:]
	t.lineStart = t.lineStart[rows:]
	for index := range t.lineStart {
		t.lineStart[index] -= words
	}
	t.lines -= rows
}

// fill is parser_buffer_bytes; false at the end of the file.
func (t *tokenizer) fill() bool {
	rest := t.source[t.next:]
	size := min(len(rest), readBuffer)
	if t.decoded {
		size = 0
		for count := 0; count < readBuffer && size < len(rest); count++ {
			_, width := utf8.DecodeRune(rest[size:])
			size += width
		}
	}
	t.data, t.datapos, t.blankStart = rest[:size], 0, 0
	t.next += size
	if size > 0 {
		t.makeStreamSpace(size)
	}
	return size > 0
}

func isBlank(c byte) bool { return c == ' ' || c == '\t' }

// tokenizeRows is tokenize_nrows.
func (t *tokenizer) tokenizeRows(rows int) error {
	start := t.lines
	if t.state == tokFinished {
		return nil
	}
	for t.lines-start < rows {
		if t.datapos == len(t.data) {
			if !t.fill() {
				err := t.handleEOF()
				t.state = tokFinished
				return err
			}
		}
		if err := t.tokenizeBytes(start + rows); err != nil {
			return err
		}
	}
	return nil
}

// handleEOF is parser_handle_eof.
func (t *tokenizer) handleEOF() error {
	switch t.state {
	case tokStartRecord, tokWhitespaceLine, tokEatCRNLNop:
		return nil
	case tokInQuoted:
		return ErrParser
	case tokInField, tokStartField, tokQuoteInQuoted:
		if err := t.endField(); err != nil {
			return err
		}
	}
	return t.endLine()
}

// tokenizeBytes is tokenize_bytes: it stops at the end of the buffer or once
// limit lines are buffered.
func (t *tokenizer) tokenizeBytes(limit int) error {
	data := t.data
	if t.fileLines == 0 && bytes.HasPrefix(data[t.datapos:], []byte{0xef, 0xbb, 0xbf}) {
		t.datapos += 3
	}
	origin := t.datapos
	for i := origin; i < len(data); i++ {
		c := data[i]
		lineEnded := false
		switch t.state {
		case tokWhitespaceLine:
			switch {
			case c == '\n':
				t.fileLines++
				t.state = tokStartRecord
			case c == '\r':
				t.fileLines++
				t.state = tokEatCRNLNop
			case isBlank(c) && c != ';':
			default:
				// Not a blank line after all: back to the character after
				// the last line feed of this buffer, or to where this call
				// began, and read from there as a field. The leading
				// whitespace then becomes the first field's text, and the
				// lines buffered since that point are appended again.
				j := i
				for j > origin && data[j] != '\n' {
					j--
				}
				if data[j] == '\n' {
					j++
				}
				if j < t.blankStart {
					t.reread = true
				}
				i = j - 1
				t.state = tokStartField
			}
		case tokStartRecord, tokStartField:
			if t.state == tokStartRecord {
				if c == '\n' {
					t.fileLines++
					break
				}
				if c == '\r' {
					t.fileLines++
					t.state = tokEatCRNLNop
					break
				}
				if isBlank(c) && c != ';' {
					t.state, t.blankStart = tokWhitespaceLine, i
					break
				}
				t.state = tokStartField
			}
			switch c {
			case '\n':
				if err := t.endField(); err != nil {
					return err
				}
				if err := t.endLine(); err != nil {
					return err
				}
				t.state, lineEnded = tokStartRecord, true
			case '\r':
				if err := t.endField(); err != nil {
					return err
				}
				t.state = tokEatCRNL
			case '"':
				t.state = tokInQuoted
			case ';':
				if err := t.endField(); err != nil {
					return err
				}
			default:
				if err := t.appendByte(c); err != nil {
					return err
				}
				t.state = tokInField
			}
		case tokInField:
			switch c {
			case '\n':
				if err := t.endField(); err != nil {
					return err
				}
				if err := t.endLine(); err != nil {
					return err
				}
				t.state, lineEnded = tokStartRecord, true
			case '\r':
				if err := t.endField(); err != nil {
					return err
				}
				t.state = tokEatCRNL
			case ';':
				if err := t.endField(); err != nil {
					return err
				}
				t.state = tokStartField
			default:
				if err := t.appendByte(c); err != nil {
					return err
				}
			}
		case tokInQuoted:
			if c == '"' {
				t.state = tokQuoteInQuoted
			} else if err := t.appendByte(c); err != nil {
				return err
			}
		case tokQuoteInQuoted:
			switch c {
			case '"':
				if err := t.appendByte(c); err != nil {
					return err
				}
				t.state = tokInQuoted
			case ';':
				if err := t.endField(); err != nil {
					return err
				}
				t.state = tokStartField
			case '\n':
				if err := t.endField(); err != nil {
					return err
				}
				if err := t.endLine(); err != nil {
					return err
				}
				t.state, lineEnded = tokStartRecord, true
			case '\r':
				if err := t.endField(); err != nil {
					return err
				}
				t.state = tokEatCRNL
			default:
				if err := t.appendByte(c); err != nil {
					return err
				}
				t.state = tokInField
			}
		case tokEatCRNL:
			if err := t.endLine(); err != nil {
				return err
			}
			lineEnded = true
			switch c {
			case '\n':
				t.state = tokStartRecord
			case ';':
				if err := t.endField(); err != nil {
					return err
				}
				t.state = tokStartField
			default:
				// A bare carriage return ended the line: read c again.
				t.state = tokStartRecord
				i--
			}
		case tokEatCRNLNop:
			t.state = tokStartRecord
			if c != '\n' && c != ';' {
				i--
			}
		}
		if lineEnded && t.lines == limit {
			t.datapos = i + 1
			return nil
		}
	}
	t.datapos = len(data)
	return nil
}
