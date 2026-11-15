// Package pandascsv reproduces pandas 2.2's C parser for
// pandas.read_csv(file, delimiter=';') (header='infer', quotechar '"',
// doublequote, skip_blank_lines, UTF-8): the tokenizer, the header row, the
// implicit index rule for the field-count check, the column naming
// ('Unnamed: <i>', '.<n>' de-duplication) and the exception pandas raises.
// subscription_import_upload's Read and ReadText are the production callers.
// Golden cases: testdata/pandas_frames.json.gz, written from pandas 2.2.3 by
// TESTS/adapters/go/pandas_csv_frames_golden.py.
//
// Read and ReadDecoded reproduce the whole DataFrame of the same call and the
// values DataFrame.iterrows() hands out. They port the C parser instead of
// describing its results: tokenizer.c's state machine, line bookkeeping and
// parser buffers (tokenizer.go), its integer and float conversions
// (numeric.go: the float parser is precise_xstrtod, which is not correctly
// rounded, and it follows the pandas wheel of the platform for the digits it
// contracts into a fused multiply-add), and TextReader's low_memory reading
// (frame.go): rows are converted in chunks of 2**20 // width rows, each column
// of a chunk becomes the first of int64 (uint64 on overflow), float64, bool
// and object that takes all its fields, NA fields upcast it, and chunks of
// different dtypes are concatenated.
//
// A whitespace-led line after a bare carriage return makes the C tokenizer
// read the lines buffered since the previous line feed, or since the start of
// the buffer, a second time. Those rows are reproduced, but when the re-read
// fills the parser's buffers the answer depends on how they grow: pandas either
// raises its ParserError ('Buffer overflow caught') or reaches the reader's
// row limit first and returns the rows read so far. That case is
// ErrBareCRBacktrack.
package pandascsv

import (
	"errors"
	"slices"
	"strconv"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrEmpty is pandas' EmptyDataError ('No columns to parse from file').
var ErrEmpty = errors.New("pandas: no columns to parse from file")

// ErrParser is pandas' ParserError: EOF inside a quoted field, or a row with
// more fields than the row before it.
var ErrParser = errors.New("pandas: error tokenizing data")

// ErrBareCRBacktrack marks the input whose outcome a whitespace-led line after
// a bare carriage return makes uncertain. The C tokenizer then reads the lines
// buffered since the previous line feed, or since the start of the buffer, a
// second time: the rows it repeats are reproduced, but when the re-read fills
// the parser's buffers pandas either raises the ParserError 'Buffer overflow
// caught - possible malformed input file.' or reaches the reader's row limit
// first and answers the rows it read so far. Which of the two, and how many
// rows the limit leaves, is not reproduced.
var ErrBareCRBacktrack = refusal.New("pandascsv: whitespace line after a bare carriage return")

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
