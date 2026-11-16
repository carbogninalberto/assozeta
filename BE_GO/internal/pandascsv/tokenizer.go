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

// The capacities parser_init gives its buffers: STREAM_INIT_SIZE bytes of
// stream, and STREAM_INIT_SIZE/10 words and lines.
const (
	streamInitSize = 32
	wordsInitSize  = streamInitSize / 10
)

// tokenizer is tokenizer.c's parser_t: the token stream of the lines that are
// buffered and the state that survives between tokenize calls.
type tokenizer struct {
	source  []byte
	next    int  // offset of the next read() in source
	decoded bool // buffers hold readBuffer characters, not bytes

	data    []byte // the current buffer
	datapos int    // where the next tokenize call resumes in it
	datalen int

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
}

func newTokenizer(source []byte, decoded bool) *tokenizer {
	return &tokenizer{
		source: source, decoded: decoded,
		streamCap: streamInitSize, wordsCap: wordsInitSize, linesCap: wordsInitSize,
		lineStart: []int{0}, lineFields: []int{0},
	}
}

// word is the C string of word index: its bytes up to its NUL. end_field can
// leave a word unterminated when the stream is full; C's strlen then reads
// past the buffer, which the bytes left in the stream stand in for.
func (t *tokenizer) word(index int) []byte {
	start := t.wordStarts[index]
	if end := bytes.IndexByte(t.stream[start:], 0); end >= 0 {
		return t.stream[start : start+end]
	}
	return t.stream[start:]
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

// overflow is the ParserError a full buffer raises.
func (t *tokenizer) overflow() error { return ErrParser }

// growBuffer is grow_buffer: the capacity doubles, from the capacity it has,
// while it cannot hold length+space elements.
func growBuffer(capacity, length, space int) int {
	for length+space >= capacity {
		capacity *= 2
	}
	return capacity
}

// makeStreamSpace is make_stream_space: room for nbytes more bytes of fields,
// and for as many more words and lines. It is called at the start of every
// tokenize call with the bytes of the buffer that are left, and when end_line
// pads a short line.
func (t *tokenizer) makeStreamSpace(nbytes int) {
	t.streamCap = growBuffer(t.streamCap, t.streamLen, 2*nbytes)
	// grow_buffer(words, ...) reserves room for the words nbytes hold, unless
	// parser_trim_buffers raised max_words_cap, the largest capacity the words
	// ever had, above the words buffered: then it reserves from there. Trimming
	// is what raises it, and the low_memory path trims only after the last
	// tokenize call, so max_words_cap is parser_init's capacity.
	length := len(t.wordStarts)
	if length+nbytes < wordsInitSize {
		length = wordsInitSize - nbytes - 1
	}
	t.wordsCap = growBuffer(t.wordsCap, length, nbytes)
	t.linesCap = growBuffer(t.linesCap, t.lines+1, nbytes)
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
// field count. A full stream does not fail it, because end_field ignores
// push_char's error: the NUL is not written and the word then starts where
// the next field's bytes go, so the two read the same text.
func (t *tokenizer) endField() error {
	if len(t.wordStarts) >= t.wordsCap {
		return t.overflow()
	}
	if t.streamLen < t.streamCap {
		t.stream = append(t.stream, 0)
		t.streamLen++
	}
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
		// The bytes of the last deleted word: its text up to its NUL,
		// which strlen also stops at when the field holds one.
		start := t.wordStarts[words-1]
		chars := start + len(t.word(words-1)) + 1
		t.streamLen -= chars - t.streamBase
		t.streamBase = chars
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
	t.data, t.datapos, t.datalen = rest[:size], 0, size
	t.next += size
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
		if t.datapos == t.datalen {
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
// limit lines are buffered. i is the C loop's byte, so a state that reads one
// again leaves i where the for statement takes it back to that byte.
func (t *tokenizer) tokenizeBytes(limit int) error {
	t.makeStreamSpace(t.datalen - t.datapos)
	data := t.data
	if t.fileLines == 0 && bytes.HasPrefix(data[t.datapos:], []byte{0xef, 0xbb, 0xbf}) {
		t.datapos += 3
	}
	for i := t.datapos; i < t.datalen; i++ {
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
				// Not a blank line after all: step back to the first byte
				// after the last line feed of this call, or to where the
				// call began, and read from there as a field. The leading
				// whitespace becomes the field's text, and the lines
				// buffered since that point are appended again.
				for {
					i--
					// buf is one byte after i: the loop tests that byte,
					// and the byte past the buffer is the C string's NUL.
					if i+1 <= t.datapos || i+1 < t.datalen && data[i+1] == '\n' {
						break
					}
				}
				if i+1 < t.datalen && data[i+1] == '\n' {
					i++
				}
				t.state = tokStartField
			}
		case tokStartRecord, tokStartField:
			if t.state == tokStartRecord {
				switch {
				case c == '\n':
					t.fileLines++
					continue
				case c == '\r':
					t.fileLines++
					t.state = tokEatCRNLNop
					continue
				case isBlank(c) && c != ';':
					t.state = tokWhitespaceLine
					continue
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
	t.datapos = t.datalen
	return nil
}
