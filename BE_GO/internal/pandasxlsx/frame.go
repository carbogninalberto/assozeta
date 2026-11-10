// Package pandasxlsx reproduces pandas 2.2's
// read_excel(file, engine="openpyxl") (openpyxl 3.1 read-only, data-only
// workbook, first worksheet, header row 0, default NA values) for the
// workbooks spreadsheet applications write: the cell values openpyxl yields,
// pandas' row trimming, the python parser's column names and per-column type
// inference, and the values DataFrame.iterrows() hands out. Anything outside
// the modelled subset is ErrUnsupported, never a guess. Golden cases:
// testdata/pandas_frames.json (TESTS/adapters/go/pandas_xlsx_golden.py).
package pandasxlsx

import (
	"errors"
	"math"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrInvalid is input read_excel raises on: not a ZIP archive, or an archive
// without '[Content_Types].xml'.
var ErrInvalid = errors.New("pandasxlsx: not a workbook")

// ErrUnsupported marks a workbook outside the modelled subset.
var ErrUnsupported = refusal.New("pandasxlsx: workbook outside the supported subset")

// Kind is the Python type of a value iterrows() yields.
type Kind int

const (
	// NaN is float('nan'): an empty cell or an NA text.
	NaN Kind = iota
	// String is a str.
	String
	// Int is a Python int.
	Int
	// Float is a float (numpy.float64 in an all-numeric frame).
	Float
	// Time is a pandas Timestamp or a datetime.datetime: both print as
	// 'YYYY-MM-DD HH:MM:SS[.ffffff]', are true and are not JSON serializable.
	Time
	// NaT is pandas.NaT: an empty cell of a datetime64 column.
	NaT
)

// Value is one cell of a row.
type Value struct {
	Kind Kind
	S    string
	I    int64
	F    float64
	T    time.Time
}

// TimeText is str() of a Time value.
func (v Value) TimeText() string {
	text := v.T.Format("2006-01-02 15:04:05")
	if micro := v.T.Nanosecond() / 1000; micro != 0 {
		text += "." + leftPad(strconv.Itoa(micro), 6)
	}
	return text
}

func leftPad(text string, width int) string {
	return strings.Repeat("0", max(width-len(text), 0)) + text
}

// Frame is the DataFrame: its columns and the rows of iterrows().
type Frame struct {
	Columns []string
	Rows    [][]Value
}

// naValues is pandas' STR_NA_VALUES.
var naValues = map[string]bool{"": true, "#N/A": true, "#N/A N/A": true, "#NA": true, "-1.#IND": true, "-1.#QNAN": true,
	"-NaN": true, "-nan": true, "1.#IND": true, "1.#QNAN": true, "<NA>": true, "N/A": true, "NA": true, "NULL": true,
	"NaN": true, "None": true, "n/a": true, "nan": true, "null": true}

var boolTexts = map[string]bool{"True": true, "TRUE": true, "true": true, "False": true, "FALSE": true, "false": true}

type cellKind int

const (
	cellEmpty cellKind = iota
	cellString
	cellInt
	cellFloat
	cellTime
)

type cell struct {
	kind cellKind
	s    string
	i    int64
	f    float64
	t    time.Time
}

func (c cell) blank() bool { return c.kind == cellEmpty || c.kind == cellString && c.s == "" }

// isASCIISpace is the C tokenizer's isspace_ascii.
func isASCIISpace(c byte) bool { return c == ' ' || c >= '\t' && c <= '\r' }

type numeric struct {
	integer bool
	i       int64
	f       float64
}

// numericText is maybe_convert_numeric for one str: whitespace, a sign,
// digits with an optional fraction. Forms whose value the conversion does not
// round as strconv does (exponents, more than 15 digits with a fraction),
// integers beyond int64 and the inf/nan words are unsupported.
func numericText(text string) (numeric, bool, error) {
	start, end := 0, len(text)
	for start < end && isASCIISpace(text[start]) {
		start++
	}
	for end > start && isASCIISpace(text[end-1]) {
		end--
	}
	body := text[start:end]
	unsigned := strings.TrimLeft(body, "+-")
	switch strings.ToLower(unsigned) {
	case "inf", "infinity", "nan":
		if len(body)-len(unsigned) <= 1 {
			return numeric{}, false, ErrUnsupported
		}
	}
	rest := body
	if rest != "" && (rest[0] == '+' || rest[0] == '-') {
		rest = rest[1:]
	}
	digits, dots, exponent := 0, 0, false
	for i := 0; i < len(rest); i++ {
		c := rest[i]
		switch {
		case c >= '0' && c <= '9':
			digits++
		case c == '.':
			dots++
		case c == 'e' || c == 'E':
			exponent = true
			tail := rest[i+1:]
			if tail != "" && (tail[0] == '+' || tail[0] == '-') {
				tail = tail[1:]
			}
			if tail == "" || strings.Trim(tail, "0123456789") != "" {
				return numeric{}, false, nil
			}
			i = len(rest)
		default:
			return numeric{}, false, nil
		}
	}
	if digits == 0 || dots > 1 {
		return numeric{}, false, nil
	}
	if exponent {
		if mantissa, _, _ := strings.Cut(strings.ToLower(rest), "e"); strings.Trim(mantissa, "0123456789.") != "" || strings.Trim(mantissa, ".") == "" {
			return numeric{}, false, nil
		}
		return numeric{}, false, ErrUnsupported
	}
	if dots == 0 {
		value, err := strconv.ParseInt(body, 10, 64)
		if err != nil {
			return numeric{}, false, ErrUnsupported
		}
		return numeric{integer: true, i: value}, true, nil
	}
	if digits > 15 {
		return numeric{}, false, ErrUnsupported
	}
	value, err := strconv.ParseFloat(body, 64)
	if err != nil {
		return numeric{}, false, ErrUnsupported
	}
	return numeric{f: value}, true, nil
}

// Timestamp.max: later datetimes keep their column an object column.
var timestampMax = time.Date(2262, 4, 11, 23, 47, 16, 854775807, time.UTC)

const (
	columnObject = iota
	columnInt
	columnFloat
	columnTime
)

// build is the python parser on get_sheet_data's rows (trimmed and padded).
func build(rows [][]cell) (*Frame, error) {
	last := -1
	for index, row := range rows {
		for len(row) > 0 && row[len(row)-1].blank() {
			row = row[:len(row)-1]
		}
		rows[index] = row
		if len(row) > 0 {
			last = index
		}
	}
	rows = rows[:last+1]
	if len(rows) == 0 {
		return &Frame{Columns: []string{}}, nil
	}
	width := 0
	for _, row := range rows {
		width = max(width, len(row))
	}
	for index, row := range rows {
		for len(row) < width {
			row = append(row, cell{})
		}
		rows[index] = row
	}
	if width == 1 {
		// The parser drops single-cell lines that are blank text.
		for _, row := range rows {
			if row[0].kind == cellEmpty || row[0].kind == cellString && strings.TrimSpace(row[0].s) == "" {
				return nil, ErrUnsupported
			}
		}
	}
	header := make([]string, width)
	for index, value := range rows[0] {
		if value.kind != cellEmpty && value.kind != cellString {
			return nil, ErrUnsupported
		}
		header[index] = value.s
	}
	frame := &Frame{Columns: columnNames(header), Rows: make([][]Value, len(rows)-1)}
	for index := range frame.Rows {
		frame.Rows[index] = make([]Value, width)
	}
	kinds := make([]int, width)
	for column := 0; column < width; column++ {
		kind, err := inferColumn(rows[1:], column, frame.Rows)
		if err != nil {
			return nil, err
		}
		kinds[column] = kind
	}
	if len(frame.Rows) == 0 {
		return frame, nil
	}
	// iterrows(): one dtype per row. Numeric columns only give float64 rows.
	numericOnly, integerOnly := true, true
	for _, kind := range kinds {
		numericOnly = numericOnly && (kind == columnInt || kind == columnFloat)
		integerOnly = integerOnly && kind == columnInt
	}
	if integerOnly {
		return nil, ErrUnsupported // numpy.int64 values
	}
	if numericOnly {
		for _, row := range frame.Rows {
			for index, value := range row {
				if value.Kind == Int {
					row[index] = Value{Kind: Float, F: float64(value.I)}
				}
			}
		}
	}
	// A row of datetimes and missing values only is a datetime64 Series:
	// its NaN are NaT (unless a datetime is beyond Timestamp.max).
	for _, row := range frame.Rows {
		datetimes, convertible := false, true
		for _, value := range row {
			switch value.Kind {
			case NaT:
				datetimes = true
			case Time:
				datetimes = true
				convertible = convertible && !value.T.After(timestampMax)
			case NaN:
			default:
				convertible = false
			}
		}
		if datetimes && convertible {
			for index, value := range row {
				if value.Kind == NaN {
					row[index] = Value{Kind: NaT}
				}
			}
		}
	}
	return frame, nil
}

// inferColumn is _infer_types and the DataFrame constructor for one column.
func inferColumn(rows [][]cell, column int, out [][]Value) (int, error) {
	numbers := make([]numeric, len(rows))
	allNumeric, anyFloat, anyNA, anyValue := true, false, false, false
	allTime, anyTime, timesInBounds := true, false, true
	allBoolText := true
	var numericErr error
	for index, row := range rows {
		value := row[column]
		switch value.kind {
		case cellEmpty:
			anyNA = true
			continue
		case cellString:
			if naValues[value.s] {
				anyNA = true
				continue
			}
			allTime = false
			allBoolText = allBoolText && boolTexts[value.s]
			if allNumeric {
				number, ok, err := numericText(value.s)
				if err != nil && numericErr == nil {
					numericErr = err
				}
				if err == nil && !ok {
					allNumeric = false
				}
				numbers[index] = number
				anyFloat = anyFloat || ok && !number.integer
			}
		case cellInt:
			allTime, allBoolText = false, false
			numbers[index] = numeric{integer: true, i: value.i}
		case cellFloat:
			allTime, allBoolText = false, false
			numbers[index] = numeric{f: value.f}
			anyFloat = true
		case cellTime:
			allNumeric, allBoolText = false, false
			anyTime = true
			timesInBounds = timesInBounds && !value.t.After(timestampMax)
		}
		anyValue = true
	}
	if !anyValue {
		for _, row := range out {
			row[column] = Value{Kind: NaN}
		}
		return columnFloat, nil
	}
	if allNumeric {
		if numericErr != nil {
			return 0, numericErr
		}
		float := anyFloat || anyNA
		for index, row := range rows {
			value := row[column]
			switch {
			case value.kind == cellEmpty || value.kind == cellString && naValues[value.s]:
				out[index][column] = Value{Kind: NaN}
			case !float:
				out[index][column] = Value{Kind: Int, I: numbers[index].i}
			case numbers[index].integer:
				out[index][column] = Value{Kind: Float, F: float64(numbers[index].i)}
			default:
				if math.IsInf(numbers[index].f, 0) || math.IsNaN(numbers[index].f) {
					return 0, ErrUnsupported
				}
				out[index][column] = Value{Kind: Float, F: numbers[index].f}
			}
		}
		if float {
			return columnFloat, nil
		}
		return columnInt, nil
	}
	if allBoolText {
		return 0, ErrUnsupported // maybe_convert_bool
	}
	datetime := allTime && anyTime && timesInBounds
	for index, row := range rows {
		value := row[column]
		switch value.kind {
		case cellEmpty:
			out[index][column] = Value{Kind: NaN}
			if datetime {
				out[index][column] = Value{Kind: NaT}
			}
		case cellString:
			out[index][column] = Value{Kind: String, S: value.s}
			if naValues[value.s] {
				out[index][column] = Value{Kind: NaN}
				if datetime {
					out[index][column] = Value{Kind: NaT}
				}
			}
		case cellInt:
			out[index][column] = Value{Kind: Int, I: value.i}
		case cellFloat:
			out[index][column] = Value{Kind: Float, F: value.f}
		case cellTime:
			out[index][column] = Value{Kind: Time, T: value.t}
		}
	}
	if datetime {
		return columnTime, nil
	}
	return columnObject, nil
}

// columnNames is the parser's header naming: an empty name is
// 'Unnamed: <i>'; duplicates get '.<n>', named columns first.
func columnNames(header []string) []string {
	columns := append([]string(nil), header...)
	unnamed := map[int]bool{}
	for index, name := range columns {
		if name == "" {
			columns[index] = "Unnamed: " + strconv.Itoa(index)
			unnamed[index] = true
		}
	}
	order := make([]int, 0, len(columns))
	for index := range columns {
		if !unnamed[index] {
			order = append(order, index)
		}
	}
	for index := range columns {
		if unnamed[index] {
			order = append(order, index)
		}
	}
	contains := func(name string) bool {
		for _, column := range columns {
			if column == name {
				return true
			}
		}
		return false
	}
	counts := map[string]int{}
	for _, index := range order {
		column := columns[index]
		original := column
		count := counts[column]
		for count > 0 {
			counts[original] = count + 1
			column = original + "." + strconv.Itoa(count)
			if contains(column) {
				count++
			} else {
				count = counts[column]
			}
		}
		columns[index] = column
		counts[column] = count + 1
	}
	return columns
}
