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

	// blankStart is where the whitespace that opens the current line began
	// in the buffer (0 when it began in an earlier one).
	blankStart int
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

func (t *tokenizer) endField() {
	t.stream = append(t.stream, 0)
	t.wordStarts = append(t.wordStarts, t.pending)
	t.lineFields[t.lines]++
	t.pending = len(t.stream)
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
	if t.lines >= 1 {
		for ; fields < expected; fields++ {
			t.endField()
		}
	}
	t.fileLines++
	t.lines++
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
		t.endField()
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
				// Back to the character after the last line feed of this
				// buffer, or to where this call began.
				j := i
				for j > origin && data[j] != '\n' {
					j--
				}
				if data[j] == '\n' {
					j++
				}
				if j < t.blankStart {
					// Only after a bare carriage return: the tokenizer
					// reads the lines since the last line feed again, up
					// to its row limit or the end of its buffers.
					return ErrUnsupported
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
				t.endField()
				if err := t.endLine(); err != nil {
					return err
				}
				t.state, lineEnded = tokStartRecord, true
			case '\r':
				t.endField()
				t.state = tokEatCRNL
			case '"':
				t.state = tokInQuoted
			case ';':
				t.endField()
			default:
				t.stream = append(t.stream, c)
				t.state = tokInField
			}
		case tokInField:
			switch c {
			case '\n':
				t.endField()
				if err := t.endLine(); err != nil {
					return err
				}
				t.state, lineEnded = tokStartRecord, true
			case '\r':
				t.endField()
				t.state = tokEatCRNL
			case ';':
				t.endField()
				t.state = tokStartField
			default:
				t.stream = append(t.stream, c)
			}
		case tokInQuoted:
			if c == '"' {
				t.state = tokQuoteInQuoted
			} else {
				t.stream = append(t.stream, c)
			}
		case tokQuoteInQuoted:
			switch c {
			case '"':
				t.stream = append(t.stream, c)
				t.state = tokInQuoted
			case ';':
				t.endField()
				t.state = tokStartField
			case '\n':
				t.endField()
				if err := t.endLine(); err != nil {
					return err
				}
				t.state, lineEnded = tokStartRecord, true
			case '\r':
				t.endField()
				t.state = tokEatCRNL
			default:
				t.stream = append(t.stream, c)
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
				t.endField()
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
