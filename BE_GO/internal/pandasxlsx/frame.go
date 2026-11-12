// Package pandasxlsx reproduces pandas 2.2's
// read_excel(file, engine="openpyxl") (openpyxl 3.1 read-only, data-only
// workbook, first worksheet, header row 0, default NA values) for the
// workbooks spreadsheet applications write: the cell values openpyxl yields
// (text, numbers, booleans, errors, dates, times and durations in the 1900
// and 1904 date systems, ISO 8601 date cells), pandas' row trimming, the
// python parser's column names and per-column type inference, and the values
// DataFrame.iterrows() hands out. Anything outside the modelled subset is
// ErrUnsupported, never a guess. Golden cases: testdata/pandas_frames.json.gz,
// testdata/pandas_text_frames.json.gz and testdata/numeric_texts.json.gz,
// written by TESTS/adapters/go/pandas_xlsx_golden.py from pandas itself.
package pandasxlsx

import (
	"errors"
	"math"
	"math/big"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrInvalid is input read_excel raises on: not a ZIP archive, or an archive
// without '[Content_Types].xml'.
var ErrInvalid = errors.New("pandasxlsx: not a workbook")

// ErrUnsupported marks a workbook outside the modelled subset.
var ErrUnsupported = refusal.New("pandasxlsx: workbook outside the supported subset")

// Frame is the DataFrame: its column labels and the rows of iterrows().
type Frame struct {
	// Columns is str() of each label; a text label is the label itself.
	Columns []string
	// Labels is columns.tolist().
	Labels []Value
	Rows   [][]Value
	// Index is the class of the column Index the labels make.
	Index IndexClass
}

// TextLabels reports whether every column label is a str.
func (f *Frame) TextLabels() bool {
	for _, label := range f.Labels {
		if label.Kind != String {
			return false
		}
	}
	return true
}

// Read is ReadText without text columns.
func Read(data []byte) (*Frame, error) { return ReadText(data, nil) }

// ReadText is ReadAll for the frames made of text labels and of str, int,
// finite float, datetime, nan and NaT values (the kinds NaN, String, Int,
// Float, Time and NaT; a numpy.int64 is the Int the import takes with
// item()); any other frame is ErrUnsupported.
func ReadText(data []byte, textual func(column string) bool) (*Frame, error) {
	frame, err := ReadAll(data, textual)
	if err != nil {
		return nil, err
	}
	if !frame.TextLabels() {
		return nil, ErrUnsupported
	}
	for _, row := range frame.Rows {
		for index, value := range row {
			switch value.Kind {
			case NaN, String, Int, Time, NaT:
			case Int64:
				row[index].Kind = Int
			case Float:
				if math.IsInf(value.F, 0) {
					return nil, ErrUnsupported
				}
			default:
				return nil, ErrUnsupported
			}
		}
	}
	return frame, nil
}

// naValues is pandas' STR_NA_VALUES.
var naValues = map[string]bool{"": true, "#N/A": true, "#N/A N/A": true, "#NA": true, "-1.#IND": true, "-1.#QNAN": true,
	"-NaN": true, "-nan": true, "1.#IND": true, "1.#QNAN": true, "<NA>": true, "N/A": true, "NA": true, "NULL": true,
	"NaN": true, "None": true, "n/a": true, "nan": true, "null": true}

// boolTexts are maybe_convert_bool's default true and false values.
var boolTexts = map[string]bool{"True": true, "TRUE": true, "true": true, "False": false, "FALSE": false, "false": false}

type cellKind int

const (
	cellEmpty cellKind = iota // '' (no value)
	cellString
	cellInt
	cellFloat
	cellTime     // datetime.datetime
	cellBool     // bool
	cellNaN      // numpy.nan: an error cell
	cellClock    // datetime.time
	cellDelta    // datetime.timedelta
	cellCalendar // datetime.date
)

// cell is a value pandas' _convert_cell returns.
type cell struct {
	kind cellKind
	s    string
	i    int64
	n    *big.Int // a cellInt outside int64
	f    float64
	b    bool
	t    time.Time
	d    time.Duration
}

func (c cell) blank() bool { return c.kind == cellEmpty || c.kind == cellString && c.s == "" }

func intCell(value *big.Int) cell {
	if value.IsInt64() {
		return cell{kind: cellInt, i: value.Int64()}
	}
	return cell{kind: cellInt, n: value}
}

// str is Python's str() of the cell.
func (c cell) str() string { return c.value().Str() }

// value is the cell as a Python object.
func (c cell) value() Value {
	switch c.kind {
	case cellString:
		return Value{Kind: String, S: c.s}
	case cellInt:
		if c.n != nil {
			return Value{Kind: BigInt, S: c.n.String()}
		}
		return Value{Kind: Int, I: c.i}
	case cellFloat:
		return Value{Kind: Float, F: c.f}
	case cellTime:
		return Value{Kind: Time, T: c.t}
	case cellBool:
		return Value{Kind: Bool, B: c.b}
	case cellNaN:
		return Value{Kind: NaN}
	case cellClock:
		return Value{Kind: TimeOfDay, D: c.d}
	case cellDelta:
		return Value{Kind: PyTimedelta, D: c.d}
	case cellCalendar:
		return Value{Kind: Date, T: c.t}
	}
	return Value{Kind: String}
}

// Timestamp.min and Timestamp.max: datetimes outside them keep their column
// an object column.
var (
	timestampMin = time.Date(1677, 9, 21, 0, 12, 43, 145224193, time.UTC)
	timestampMax = time.Date(2262, 4, 11, 23, 47, 16, 854775807, time.UTC)
)

func inTimestampBounds(moment time.Time) bool {
	return !moment.Before(timestampMin) && !moment.After(timestampMax)
}

// The dtype of a column.
const (
	typeObject = iota
	typeInt64
	typeUint64
	typeFloat
	typeBool
	typeDatetime
	typeTimedelta
)

type column struct {
	dtype  int
	values []Value
}

// build is the python parser on get_sheet_data's rows (trimmed and padded),
// the DataFrame constructor and iterrows().
func build(rows [][]cell, text func(column string) bool) (*Frame, error) {
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
		return &Frame{Columns: []string{}, Labels: []Value{}}, nil
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
	labels, class, err := columnLabels(rows[0])
	if err != nil {
		return nil, err
	}
	frame := &Frame{Labels: labels, Index: class, Columns: make([]string, width), Rows: make([][]Value, len(rows)-1)}
	for index, label := range labels {
		frame.Columns[index] = label.Str()
	}
	if len(frame.Rows) == 0 {
		return frame, nil
	}
	columns := make([]column, width)
	for index := range columns {
		// A converter is found by its name: a label that is a str.
		if text != nil && labels[index].Kind == String && text(labels[index].S) {
			columns[index] = textColumn(rows[1:], index)
		} else if columns[index], err = inferColumn(rows[1:], index); err != nil {
			return nil, err
		}
	}
	// DataFrame.values: one dtype for the frame. Equal dtypes keep theirs,
	// int64, uint64 and float64 give float64, anything else object.
	common, numbers := columns[0].dtype, true
	for _, item := range columns {
		if item.dtype != common {
			common = typeObject
		}
		numbers = numbers && (item.dtype == typeInt64 || item.dtype == typeUint64 || item.dtype == typeFloat)
	}
	if common == typeObject && numbers {
		common = typeFloat
	}
	for index := range frame.Rows {
		row := make([]Value, width)
		for position, item := range columns {
			row[position] = scalar(item.values[index], item.dtype, common)
		}
		if common == typeObject {
			inferRow(row)
		}
		frame.Rows[index] = row
	}
	return frame, nil
}

// scalar is the element of a column in the frame's array.
func scalar(value Value, dtype, common int) Value {
	switch common {
	case typeInt64:
		return Value{Kind: Int64, I: value.I}
	case typeBool:
		return Value{Kind: NumpyBool, B: value.B}
	case typeFloat:
		switch value.Kind {
		case Int:
			return Value{Kind: Float, F: float64(value.I)}
		case Uint64:
			return Value{Kind: Float, F: float64(value.U)}
		}
	case typeObject:
		if value.Kind == Uint64 {
			if value.U > math.MaxInt64 {
				return Value{Kind: BigInt, S: strconv.FormatUint(value.U, 10)}
			}
			return Value{Kind: Int, I: int64(value.U)}
		}
	}
	return value
}

// inferRow is the Series constructor on a row of objects
// (maybe_infer_to_datetimelike): datetimes and missing values only are a
// datetime64 Series, timedeltas and missing values only a timedelta64 one,
// NaT and nan only are all NaT.
func inferRow(row []Value) {
	present, anyNaT, datetimes, timedeltas := 0, false, true, true
	for _, value := range row {
		switch value.Kind {
		case NaN:
		case NaT:
			anyNaT = true
		case Time:
			present++
			timedeltas = false
			datetimes = datetimes && inTimestampBounds(value.T)
		case Timedelta, PyTimedelta:
			present++
			datetimes = false
		default:
			present++
			datetimes, timedeltas = false, false
		}
	}
	if present == 0 && !anyNaT || present > 0 && !datetimes && !timedeltas {
		return
	}
	for index, value := range row {
		switch value.Kind {
		case NaN:
			row[index] = Value{Kind: NaT}
		case PyTimedelta:
			row[index].Kind = Timedelta
		}
	}
}

// textColumn is a column read through the text converter (map_infer, then
// _infer_types without the numeric and boolean readings): an int or a float
// is its str() (an error cell 'nan', which is an NA text), every other cell
// stays as it is. Booleans only are a bool column; otherwise the NA texts
// are nan and the column holds objects, datetime64 when every value is a
// datetime within Timestamp's range, timedelta64 when every value is a
// timedelta.
func textColumn(rows [][]cell, index int) column {
	out := column{values: make([]Value, len(rows))}
	booleans, datetimes, timedeltas, present := len(rows) > 0, true, true, 0
	for position, row := range rows {
		value := row[index]
		switch {
		case value.kind == cellEmpty || value.kind == cellNaN || value.kind == cellString && naValues[value.s]:
			out.values[position] = Value{Kind: NaN}
			booleans = false
			continue
		case value.kind == cellInt || value.kind == cellFloat:
			out.values[position] = Value{Kind: String, S: value.str()}
		default:
			out.values[position] = value.value()
		}
		present++
		booleans = booleans && value.kind == cellBool
		datetimes = datetimes && value.kind == cellTime && inTimestampBounds(value.t)
		timedeltas = timedeltas && value.kind == cellDelta
	}
	switch {
	case booleans:
		out.dtype = typeBool
	case present > 0 && datetimes:
		out.dtype = typeDatetime
	case present > 0 && timedeltas:
		out.dtype = typeTimedelta
	}
	if out.dtype == typeDatetime || out.dtype == typeTimedelta {
		for position := range out.values {
			switch out.values[position].Kind {
			case NaN:
				out.values[position].Kind = NaT
			case PyTimedelta:
				out.values[position].Kind = Timedelta
			}
		}
	}
	return out
}

// inferColumn is _infer_types (maybe_convert_numeric, then
// maybe_convert_bool) and the DataFrame constructor's datetime inference for
// one column. The values are those of the column's own dtype.
func inferColumn(rows [][]cell, index int) (column, error) {
	out := column{values: make([]Value, len(rows))}
	numbers := make([]numeric, len(rows))
	var null, float, integer, unsigned, signed bool
	numericColumn := true
	var uncertain error
	sawInt := func(value *big.Int) {
		integer = true
		switch {
		case value.Cmp(minInt64) < 0 || value.Cmp(maxUint64) > 0:
			float = true
		case value.Sign() < 0:
			signed = true
		case value.Cmp(maxInt64) > 0:
			unsigned = true
		}
	}
	for position, row := range rows {
		value := row[index]
		switch value.kind {
		case cellEmpty, cellNaN:
			null, float = true, true
		case cellString:
			if naValues[value.s] {
				null, float = true, true
				continue
			}
			number, ok, err := numericText(value.s)
			switch {
			case err != nil:
				uncertain = err
			case !ok:
				numericColumn = false
			case number.integer:
				sawInt(number.i)
			default:
				float = true
			}
			numbers[position] = number
		case cellInt:
			if value.n != nil {
				sawInt(value.n)
			} else {
				integer = true
				signed = signed || value.i < 0
			}
		case cellFloat:
			float = true
		case cellBool:
		default:
			numericColumn = false
		}
	}
	if !numericColumn {
		return objectColumn(rows, index, out), nil
	}
	if uncertain != nil {
		return column{}, uncertain
	}
	if unsigned && (null || signed) {
		// A uint64 next to a missing or negative value: the values stay as
		// read, the empty cells as ''.
		for position, row := range rows {
			out.values[position] = row[index].value()
		}
		return out, nil
	}
	for position, row := range rows {
		value := row[index]
		var result Value
		switch {
		case value.kind == cellEmpty || value.kind == cellNaN || value.kind == cellString && naValues[value.s]:
			result = Value{Kind: NaN}
		case float:
			switch value.kind {
			case cellString:
				if numbers[position].uncertain {
					return column{}, ErrUnsupported
				}
				result = Value{Kind: Float, F: numbers[position].f}
			case cellInt:
				result = Value{Kind: Float, F: float64(value.i)}
				if value.n != nil {
					if value.n.BitLen() > 1024 {
						return column{}, ErrUnsupported // beyond a float: OverflowError
					}
					result.F, _ = new(big.Float).SetInt(value.n).Float64()
				}
			case cellBool:
				if value.b {
					result = Value{Kind: Float, F: 1}
				} else {
					result = Value{Kind: Float}
				}
			default:
				result = Value{Kind: Float, F: value.f}
			}
		case integer:
			whole := new(big.Int)
			switch value.kind {
			case cellString:
				whole = numbers[position].i
			case cellInt:
				whole.SetInt64(value.i)
				if value.n != nil {
					whole = value.n
				}
			case cellBool:
				if value.b {
					whole.SetInt64(1)
				}
			}
			if unsigned {
				result = Value{Kind: Uint64, U: whole.Uint64()}
			} else {
				result = Value{Kind: Int, I: whole.Int64()}
			}
		default:
			result = Value{Kind: Bool, B: value.b}
		}
		out.values[position] = result
	}
	switch {
	case float:
		out.dtype = typeFloat
	case integer && unsigned:
		out.dtype = typeUint64
	case integer:
		out.dtype = typeInt64
	default:
		out.dtype = typeBool
	}
	return out, nil
}

// objectColumn is a column maybe_convert_numeric raises on: the NA texts are
// nan and a value equal to an earlier one is that earlier object
// (sanitize_objects: 0 after False is False, True after 1 is 1); booleans and
// boolean words are a bool column
// (objects when a value is missing); datetimes only are datetime64 and
// timedeltas only timedelta64, their missing values NaT.
func objectColumn(rows [][]cell, index int, out column) column {
	missing, booleans, datetimes, timedeltas, present := false, true, true, true, 0
	memo := map[string]cell{}
	interned := make([]cell, len(rows))
	for position, row := range rows {
		value := row[index]
		if value.kind == cellInt && value.n == nil || value.kind == cellBool {
			key := labelKey(value)
			if first, seen := memo[key]; seen {
				value = first
			} else {
				memo[key] = value
			}
		}
		interned[position] = value
		if value.kind == cellEmpty || value.kind == cellNaN || value.kind == cellString && naValues[value.s] {
			out.values[position] = Value{Kind: NaN}
			missing = true
			continue
		}
		present++
		out.values[position] = value.value()
		word, isWord := boolTexts[value.s]
		switch {
		case value.kind == cellBool:
			datetimes, timedeltas = false, false
		case value.kind == cellString && isWord:
			datetimes, timedeltas = false, false
			if booleans {
				out.values[position] = Value{Kind: Bool, B: word}
			}
		case value.kind == cellTime:
			booleans, timedeltas = false, false
			datetimes = datetimes && inTimestampBounds(value.t)
		case value.kind == cellDelta:
			booleans, datetimes = false, false
		default:
			booleans, datetimes, timedeltas = false, false, false
		}
	}
	switch {
	case present == 0:
	case booleans:
		if !missing {
			out.dtype = typeBool
		}
		return out
	case datetimes:
		out.dtype = typeDatetime
	case timedeltas:
		out.dtype = typeTimedelta
	}
	for position := range rows {
		value := &out.values[position]
		switch {
		case value.Kind == Bool && interned[position].kind == cellString:
			*value = Value{Kind: String, S: interned[position].s} // not a boolean column after all
		case value.Kind == NaN && out.dtype != typeObject:
			value.Kind = NaT
		case value.Kind == PyTimedelta && out.dtype == typeTimedelta:
			value.Kind = Timedelta
		}
	}
	return out
}

// labelKey identifies a header value the way Python compares and hashes it:
// 1, 1.0 and True are one key, every nan is numpy.nan itself.
func labelKey(value cell) string {
	switch value.kind {
	case cellString:
		return "s" + value.s
	case cellInt:
		if value.n != nil {
			return "n" + value.n.String()
		}
		return "n" + strconv.FormatInt(value.i, 10)
	case cellBool:
		if value.b {
			return "n1"
		}
		return "n0"
	case cellFloat:
		return "n" + strconv.FormatFloat(value.f, 'g', -1, 64)
	case cellNaN:
		return "nan"
	case cellTime:
		return "t" + strconv.FormatInt(value.t.UnixMicro(), 10)
	case cellCalendar:
		return "d" + strconv.FormatInt(value.t.Unix(), 10)
	case cellClock:
		return "c" + strconv.FormatInt(int64(value.d), 10)
	default:
		return "e" + strconv.FormatInt(int64(value.d), 10)
	}
}

// columnLabels is the parser's header naming and the Index the DataFrame
// builds from it: an empty name is 'Unnamed: <i>'; duplicates get '.<n>'
// after their str(), named columns first; then numbers only are one numeric
// Index (int64 for whole numbers in int64, uint64 for those in [0, 2**64),
// float64 when one is a float or nan and every whole number is one of the two,
// objects otherwise), booleans only a bool one, datetimes (or timedeltas) and
// nan only a DatetimeIndex (TimedeltaIndex) with NaT, anything else keeps its
// objects.
func columnLabels(header []cell) ([]Value, IndexClass, error) {
	columns := append([]cell(nil), header...)
	unnamed := map[int]bool{}
	for index, name := range columns {
		if name.blank() {
			columns[index] = cell{kind: cellString, s: "Unnamed: " + strconv.Itoa(index)}
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
			if column.kind == cellString && column.s == name {
				return true
			}
		}
		return false
	}
	counts := map[string]int{}
	for _, index := range order {
		column := columns[index]
		original := column
		count := counts[labelKey(column)]
		for count > 0 {
			counts[labelKey(original)] = count + 1
			column = cell{kind: cellString, s: original.str() + "." + strconv.Itoa(count)}
			if contains(column.s) {
				count++
			} else {
				count = counts[labelKey(column)]
			}
		}
		columns[index] = column
		counts[labelKey(column)] = count + 1
	}
	labels := make([]Value, len(columns))
	numbers, booleans, fractional := true, true, false
	datetimes, timedeltas, present, anyBoolean := true, true, 0, false
	intsInt64, intsUint64 := true, true
	for index, column := range columns {
		anyBoolean = anyBoolean || column.kind == cellBool
		labels[index] = column.value()
		numbers = numbers && (column.kind == cellInt || column.kind == cellFloat || column.kind == cellBool || column.kind == cellNaN)
		booleans = booleans && column.kind == cellBool
		fractional = fractional || column.kind == cellFloat || column.kind == cellNaN
		if column.kind == cellInt {
			whole := new(big.Int).SetInt64(column.i)
			if column.n != nil {
				whole = column.n
			}
			intsInt64 = intsInt64 && whole.IsInt64()
			intsUint64 = intsUint64 && whole.Sign() >= 0 && whole.Cmp(maxUint64) <= 0
		}
		if column.kind != cellNaN {
			present++
			datetimes = datetimes && column.kind == cellTime && inTimestampBounds(column.t)
			timedeltas = timedeltas && column.kind == cellDelta
		}
	}
	class := ObjectIndex
	switch {
	case booleans:
		class = BoolIndex
	case numbers && !anyBoolean:
		class = numberIndex(fractional, intsInt64, intsUint64)
	case present > 0 && datetimes:
		class = DatetimeIndex
	case present > 0 && timedeltas:
		class = TimedeltaIndex
	}
	for index, column := range columns {
		switch {
		case class == FloatIndex && column.kind == cellInt:
			// The Index holds doubles: float(int), the nearest double.
			if column.n != nil {
				value, _ := new(big.Float).SetInt(column.n).Float64()
				labels[index] = Value{Kind: Float, F: value}
			} else {
				labels[index] = Value{Kind: Float, F: float64(column.i)}
			}
		case present > 0 && (datetimes || timedeltas) && column.kind == cellNaN:
			labels[index] = Value{Kind: NaT}
		case present > 0 && timedeltas && column.kind == cellDelta:
			labels[index].Kind = Timedelta
		}
	}
	// Integers that are one float64 would be one label.
	floats := map[float64]bool{}
	for _, label := range labels {
		if label.Kind == Float {
			if floats[label.F] {
				return nil, ObjectIndex, ErrUnsupported
			}
			floats[label.F] = true
		}
	}
	return labels, class, nil
}

// numberIndex is the Index numbers only make: int64 when every whole number
// fits int64, uint64 when every one of them is in [0, 2**64), float64 when one
// label is a float or nan and every whole number fits one of the two, objects
// otherwise.
func numberIndex(fractional, intsInt64, intsUint64 bool) IndexClass {
	switch {
	case fractional && (intsInt64 || intsUint64):
		return FloatIndex
	case fractional:
		return ObjectIndex
	case intsInt64:
		return IntIndex
	case intsUint64:
		return Uint64Index
	}
	return ObjectIndex
}
