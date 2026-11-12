// Package pandascsv reproduces the column names pandas 2.2's C parser gives
// pandas.read_csv(file, delimiter=';') (header='infer', quotechar '"',
// doublequote, skip_blank_lines, UTF-8): the tokenizer, the header row, the
// implicit index rule for the field-count check and the column naming
// ('Unnamed: <i>', '.<n>' de-duplication). Only the outcome needed by
// subscription_import_upload is modelled: the columns, or whether pandas
// raises. Golden cases: testdata/pandas_headers.json.
//
// Read and ReadDecoded reproduce the whole DataFrame of the same call and the
// values DataFrame.iterrows() hands out. They port the C parser instead of
// describing its results: tokenizer.c's state machine and line bookkeeping
// (tokenizer.go), its integer and float conversions (numeric.go: the float
// parser is precise_xstrtod, which is not correctly rounded), and
// TextReader's low_memory reading (frame.go): rows are converted in chunks of
// 2**20 // width rows, each column of a chunk becomes the first of int64
// (uint64 on overflow), float64, bool and object that takes all its fields,
// NA fields upcast it, and chunks of different dtypes are concatenated.
// Anything not proven identical is ErrUnsupported: a whitespace-led line
// after a bare carriage return (the tokenizer reads earlier lines again) and
// float texts whose value depends on how pandas was compiled.
// Golden cases: testdata/pandas_frames.json.gz, written from pandas 2.2.3 by
// TESTS/adapters/go/pandas_csv_frames_golden.py.
package pandascsv

import (
	"bytes"
	"errors"
	"slices"
	"strconv"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrEmpty is pandas' EmptyDataError ('No columns to parse from file').
var ErrEmpty = errors.New("pandas: no columns to parse from file")

// ErrParser is pandas' ParserError (EOF inside a quoted field, a row with
// more fields than expected).
var ErrParser = errors.New("pandas: error tokenizing data")

// ErrUnsupported marks input outside the modelled subset. For Columns:
// invalid UTF-8 (pandas raises UnicodeDecodeError), NUL bytes and a carriage
// return not followed by a line feed (after one, the tokenizer drops a
// following delimiter and its blank-line backtrack runs back to the previous
// '\n'). Read and ReadDecoded document their own, smaller set.
var ErrUnsupported = refusal.New("pandascsv: input outside the supported subset")

const (
	startRecord = iota
	startField
	inField
	inQuoted
	quoteInQuoted
	whitespaceLine
)

// tokenize returns the non-blank records of the C tokenizer.
func tokenize(data []byte) ([][]string, error) {
	var records [][]string
	var record []string
	var field []byte
	state := startRecord
	endField := func() {
		record = append(record, string(field))
		field = field[:0]
	}
	endLine := func() {
		records = append(records, record)
		record = nil
	}
	for i := 0; i < len(data); i++ {
		c := data[i]
		newline := c == '\n' || c == '\r'
		if c == '\r' && i+1 < len(data) && data[i+1] == '\n' && state != inQuoted {
			i++ // \r\n is one terminator outside quotes
		}
		switch state {
		case startRecord:
			switch {
			case newline:
				// skip_blank_lines
			case c == ' ' || c == '\t':
				state = whitespaceLine
				field = append(field, c)
			case c == '"':
				state = inQuoted
			case c == ';':
				endField()
				state = startField
			default:
				field = append(field, c)
				state = inField
			}
		case whitespaceLine:
			switch {
			case newline:
				field = field[:0]
				state = startRecord
			case c == ' ' || c == '\t':
				field = append(field, c)
			case c == ';':
				endField()
				state = startField
			default:
				// Backtracked: the leading whitespace belongs to the field.
				field = append(field, c)
				state = inField
			}
		case startField:
			switch {
			case newline:
				endField()
				endLine()
				state = startRecord
			case c == '"':
				state = inQuoted
			case c == ';':
				endField()
			default:
				field = append(field, c)
				state = inField
			}
		case inField:
			switch {
			case newline:
				endField()
				endLine()
				state = startRecord
			case c == ';':
				endField()
				state = startField
			default:
				field = append(field, c)
			}
		case inQuoted:
			if c == '"' {
				state = quoteInQuoted
			} else {
				field = append(field, c)
			}
		case quoteInQuoted:
			switch {
			case c == '"':
				field = append(field, '"')
				state = inQuoted
			case newline:
				endField()
				endLine()
				state = startRecord
			case c == ';':
				endField()
				state = startField
			default:
				field = append(field, c)
				state = inField
			}
		}
	}
	switch state {
	case inQuoted:
		return nil, ErrParser
	case startField, inField, quoteInQuoted:
		endField()
		endLine()
	}
	return records, nil
}

// Columns is read_csv(data, delimiter=';').columns.tolist().
func Columns(data []byte) ([]string, error) {
	data = bytes.TrimPrefix(data, []byte{0xef, 0xbb, 0xbf})
	if !utf8.Valid(data) || bytes.IndexByte(data, 0) >= 0 {
		return nil, ErrUnsupported
	}
	for i, c := range data {
		if c == '\r' && (i+1 == len(data) || data[i+1] != '\n') {
			return nil, ErrUnsupported
		}
	}
	records, err := tokenize(data)
	if err != nil {
		return nil, err
	}
	if len(records) == 0 {
		return nil, ErrEmpty
	}
	header := records[0]
	expected := len(header)
	if len(records) > 1 {
		// The first data row is exempt from the check; more fields than the
		// header make an implicit index and raise the expected count.
		expected = max(expected, len(records[1]))
		for _, record := range records[2:] {
			if len(record) > expected {
				return nil, ErrParser
			}
		}
	}
	return names(header), nil
}

// names is the C parser's header naming: ” is 'Unnamed: <i>'; duplicates
// get '.<n>', named columns first, then the unnamed ones.
func names(header []string) []string {
	columns := slices.Clone(header)
	var unnamed []int
	for i, name := range columns {
		if name == "" {
			columns[i] = "Unnamed: " + strconv.Itoa(i)
			unnamed = append(unnamed, i)
		}
	}
	order := make([]int, 0, len(columns))
	for i := range columns {
		if !slices.Contains(unnamed, i) {
			order = append(order, i)
		}
	}
	order = append(order, unnamed...)
	counts := map[string]int{}
	for _, i := range order {
		column := columns[i]
		original := column
		count := counts[column]
		for count > 0 {
			counts[original] = count + 1
			column = original + "." + strconv.Itoa(count)
			if slices.Contains(columns, column) {
				count++
			} else {
				count = counts[column]
			}
		}
		columns[i] = column
		counts[column] = count + 1
	}
	return columns
}
