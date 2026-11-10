package pandascsv

import (
	"errors"
	"math"
	"strconv"
	"strings"
	"unicode/utf8"
)

// ErrDecode is the UnicodeDecodeError pandas raises on bytes that are not
// UTF-8: in a column name or a text value for a raw handle, anywhere in the
// file for a decoded one.
var ErrDecode = errors.New("pandas: invalid UTF-8")

// Kind is the kind of Python value a cell holds; Value.NumPy tells a numpy
// scalar from the built-in type.
type Kind int

const (
	// NaN is float('nan') (numpy.float64 when NumPy is set): an NA text or
	// an empty or missing field.
	NaN Kind = iota
	// String is a str.
	String
	// Int is an int, or a numpy.int64.
	Int
	// Float is a float, or a numpy.float64; it is never NaN.
	Float
	// Bool is a bool, or a numpy.bool.
	Bool
	// Uint is a value of a uint64 column, in U: an int, or a numpy.uint64.
	Uint
)

// Value is one cell of a row of DataFrame.iterrows().
type Value struct {
	Kind  Kind
	NumPy bool
	S     string
	I     int64
	U     uint64
	F     float64
	B     bool
}

// TypeName is the Python type of the value: 'str', 'int', 'float', 'bool',
// 'numpy.int64', 'numpy.uint64', 'numpy.float64' or 'numpy.bool'.
func (v Value) TypeName() string {
	name := [...]string{NaN: "float", String: "str", Int: "int", Float: "float", Bool: "bool", Uint: "int"}[v.Kind]
	if !v.NumPy {
		return name
	}
	switch v.Kind {
	case Int:
		return "numpy.int64"
	case Uint:
		return "numpy.uint64"
	case Bool:
		return "numpy.bool"
	}
	return "numpy.float64"
}

// Text is str(value).
func (v Value) Text() string {
	switch v.Kind {
	case String:
		return v.S
	case Int:
		return strconv.FormatInt(v.I, 10)
	case Uint:
		return strconv.FormatUint(v.U, 10)
	case Float:
		return FloatText(v.F)
	case Bool:
		if v.B {
			return "True"
		}
		return "False"
	}
	return "nan"
}

// Truthy is bool(value); NaN is true.
func (v Value) Truthy() bool {
	switch v.Kind {
	case String:
		return v.S != ""
	case Int:
		return v.I != 0
	case Uint:
		return v.U != 0
	case Float:
		return v.F != 0
	case Bool:
		return v.B
	}
	return true
}

// JSONSerializable reports whether json.dumps encodes the value: not a
// numpy integer or a numpy.bool (numpy.float64 is a float).
func (v Value) JSONSerializable() bool {
	return !v.NumPy || v.Kind == NaN || v.Kind == Float
}

// FloatText is repr(float) and str(numpy.float64): the shortest digits that
// read back as the same double, in exponent form below 1e-4 and from 1e16.
func FloatText(value float64) string {
	switch {
	case math.IsNaN(value):
		return "nan"
	case math.IsInf(value, 1):
		return "inf"
	case math.IsInf(value, -1):
		return "-inf"
	}
	text := strconv.FormatFloat(value, 'e', -1, 64)
	exponent, _ := strconv.Atoi(text[strings.IndexByte(text, 'e')+1:])
	if exponent < -4 || exponent >= 16 {
		return text
	}
	text = strconv.FormatFloat(value, 'f', -1, 64)
	if !strings.Contains(text, ".") {
		text += ".0"
	}
	return text
}

// Dtype is the dtype of a DataFrame column.
type Dtype string

const (
	Int64   Dtype = "int64"
	Uint64  Dtype = "uint64"
	Float64 Dtype = "float64"
	BoolD   Dtype = "bool"
	Object  Dtype = "object"
)

// Frame is the DataFrame: its columns, their dtypes and the rows of
// iterrows(). A row holds one dtype, so the values of a frame whose columns
// differ are not those of its columns: integers next to floats are floats,
// and next to text every value is a built-in Python object.
type Frame struct {
	Columns []string
	Dtypes  []Dtype
	Rows    [][]Value
}

// Read is pandas.read_csv(file, delimiter=';') on the file object
// django-storages' S3 backend opens. That object has no 'mode', so pandas
// hands its bytes to the C parser undecoded: only column names and text
// values are decoded, and invalid UTF-8 there is ErrDecode.
func Read(data []byte) (*Frame, error) { return read(data, false, nil) }

// ReadText is Read with dtype={column: str} for every column name textual
// reports: such a column is not inferred. Its fields stay as written ('007',
// ' 5 ', 'True'), the NA texts are still NaN, and it is an object column even
// when all of it is NaN. A name no column has changes nothing. Of two columns
// with one name, the second (which pandas renames 'a.1') is read as text when
// either 'a' or 'a.1' is named.
func ReadText(data []byte, textual func(column string) bool) (*Frame, error) {
	return read(data, false, textual)
}

// ReadDecoded is pandas.read_csv(file, delimiter=';') on a binary file object
// that has a 'mode' (FileSystemStorage, BytesIO): pandas decodes it as strict
// UTF-8 before parsing, so invalid UTF-8 anywhere is ErrDecode.
func ReadDecoded(data []byte) (*Frame, error) { return ReadDecodedText(data, nil) }

// ReadDecodedText is ReadDecoded with ReadText's text columns.
func ReadDecodedText(data []byte, textual func(column string) bool) (*Frame, error) {
	if !utf8.Valid(data) {
		if len(data) > readBuffer {
			// A ParserError in an earlier buffer comes first; where the
			// decoder stops reading is not modelled.
			return nil, ErrUnsupported
		}
		return nil, ErrDecode
	}
	return read(data, true, textual)
}

// column is one parsed column of a chunk of rows.
type column struct {
	dtype  Dtype
	ints   []int64
	uints  []uint64
	floats []float64
	bools  []bool
	values []Value // object
}

func (c *column) size() int {
	return len(c.ints) + len(c.uints) + len(c.floats) + len(c.bools) + len(c.values)
}

// naValues is pandas' STR_NA_VALUES.
var naValues = map[string]bool{"": true, "#N/A": true, "#N/A N/A": true, "#NA": true, "-1.#IND": true, "-1.#QNAN": true,
	"-NaN": true, "-nan": true, "1.#IND": true, "1.#QNAN": true, "<NA>": true, "N/A": true, "NA": true, "NULL": true,
	"NaN": true, "None": true, "n/a": true, "nan": true, "null": true}

func isNA(word []byte) bool { return len(word) <= 8 && naValues[string(word)] }

// convert is _convert_tokens without a dtype: the first of int64 (uint64 on
// overflow), float64, bool and object that takes every field, then
// _maybe_upcast for the NA fields.
func convert(words [][]byte) (*column, error) {
	if result, ok, err := tryInt(words); ok || err != nil {
		return result, err
	}
	if result := tryFloat(words); result != nil {
		return result, nil
	}
	if result, err := tryFloatPlatform(words); err != nil {
		return result, err
	}
	if result := tryBool(words); result != nil {
		return result, nil
	}
	return texts(words, true)
}

// tryInt is the int64 step: _try_int64, and _try_uint64 after an overflow.
// ok is false when the column goes on to float64.
func tryInt(words [][]byte) (*column, bool, error) {
	ints := make([]int64, len(words))
	missing := false
	overflow := false
	for index, word := range words {
		if isNA(word) {
			missing = true
			ints[index] = math.MinInt64
			continue
		}
		value, status := parseInt64(word)
		if status == numberInvalid {
			return nil, false, nil
		}
		if status == numberOverflow {
			overflow = true
			break
		}
		ints[index] = value
	}
	if !overflow {
		if !missing {
			return &column{dtype: Int64, ints: ints}, true, nil
		}
		// The NA marker is the smallest int64: that value is lost with it.
		floats := make([]float64, len(ints))
		for index, value := range ints {
			floats[index] = float64(value)
			if value == math.MinInt64 {
				floats[index] = math.NaN()
			}
		}
		return &column{dtype: Float64, floats: floats}, true, nil
	}
	var state uintState
	uints := make([]uint64, len(words))
	for index, word := range words {
		if isNA(word) {
			state.null = true
			continue
		}
		value, status := parseUint64(&state, word)
		if status == numberInvalid {
			return nil, false, nil
		}
		if status == numberOverflow {
			result, err := texts(words, true)
			return result, true, err
		}
		uints[index] = value
	}
	if state.unsigned && (state.signed || state.null) {
		// No numeric dtype holds the column: its fields stay as they are,
		// NA texts included.
		result, err := texts(words, false)
		return result, true, err
	}
	if state.signed {
		result, err := texts(words, true)
		return result, true, err
	}
	return &column{dtype: Uint64, uints: uints}, true, nil
}

func equalFold(word []byte, text string) bool {
	return len(word) == len(text) && strings.EqualFold(string(word), text)
}

// floatField is one field of _try_double.
func floatField(word []byte) (float64, int) {
	if isNA(word) {
		return math.NaN(), floatOK
	}
	value, status := parseFloat(word)
	if status == floatOK {
		return value, floatOK
	}
	switch {
	case equalFold(word, "inf"), equalFold(word, "+inf"), equalFold(word, "infinity"), equalFold(word, "+infinity"):
		return math.Inf(1), floatOK
	case equalFold(word, "-inf"), equalFold(word, "-infinity"):
		return math.Inf(-1), floatOK
	}
	return 0, status
}

func tryFloat(words [][]byte) *column {
	floats := make([]float64, len(words))
	for index, word := range words {
		value, status := floatField(word)
		if status != floatOK {
			return nil
		}
		floats[index] = value
	}
	return &column{dtype: Float64, floats: floats}
}

// tryFloatPlatform refuses a column that is float64 only if a field pandas
// reads differently per platform is a number.
func tryFloatPlatform(words [][]byte) (*column, error) {
	platform := false
	for _, word := range words {
		switch _, status := floatField(word); status {
		case floatInvalid:
			return nil, nil
		case floatPlatform:
			platform = true
		}
	}
	if platform {
		return nil, ErrUnsupported
	}
	return nil, nil
}

func tryBool(words [][]byte) *column {
	bools := make([]bool, len(words))
	missing := false
	for index, word := range words {
		switch {
		case isNA(word):
			missing = true
		case equalFold(word, "true"):
			bools[index] = true
		case equalFold(word, "false"):
		default:
			return nil
		}
	}
	if !missing {
		return &column{dtype: BoolD, bools: bools}
	}
	values := make([]Value, len(words))
	for index, word := range words {
		values[index] = Value{Kind: Bool, B: bools[index]}
		if isNA(word) {
			values[index] = Value{Kind: NaN}
		}
	}
	return &column{dtype: Object, values: values}
}

// texts is _string_box_utf8.
func texts(words [][]byte, naFilter bool) (*column, error) {
	values := make([]Value, len(words))
	for index, word := range words {
		if naFilter && isNA(word) {
			values[index] = Value{Kind: NaN}
			continue
		}
		if !utf8.Valid(word) {
			return nil, ErrDecode
		}
		values[index] = Value{Kind: String, S: string(word)}
	}
	return &column{dtype: Object, values: values}, nil
}

// object is ndarray.astype(object): built-in Python values.
func (c *column) object() []Value {
	switch c.dtype {
	case Int64:
		values := make([]Value, len(c.ints))
		for index, value := range c.ints {
			values[index] = Value{Kind: Int, I: value}
		}
		return values
	case Uint64:
		values := make([]Value, len(c.uints))
		for index, value := range c.uints {
			values[index] = Value{Kind: Uint, U: value}
		}
		return values
	case Float64:
		values := make([]Value, len(c.floats))
		for index, value := range c.floats {
			values[index] = Value{Kind: Float, F: value}
			if math.IsNaN(value) {
				values[index] = Value{Kind: NaN}
			}
		}
		return values
	case BoolD:
		values := make([]Value, len(c.bools))
		for index, value := range c.bools {
			values[index] = Value{Kind: Bool, B: value}
		}
		return values
	}
	return c.values
}

func (c *column) float() []float64 {
	switch c.dtype {
	case Int64:
		floats := make([]float64, len(c.ints))
		for index, value := range c.ints {
			floats[index] = float64(value)
		}
		return floats
	case Uint64:
		floats := make([]float64, len(c.uints))
		for index, value := range c.uints {
			floats[index] = float64(value)
		}
		return floats
	case BoolD:
		floats := make([]float64, len(c.bools))
		for index, value := range c.bools {
			if value {
				floats[index] = 1
			}
		}
		return floats
	}
	return c.floats
}

// commonDtype is numpy.result_type over the dtypes, object if any is.
func commonDtype(dtypes map[Dtype]bool) Dtype {
	switch {
	case len(dtypes) == 1:
		for dtype := range dtypes {
			return dtype
		}
	case dtypes[Object]:
		return Object
	case dtypes[Float64], dtypes[Int64] && dtypes[Uint64]:
		return Float64
	case dtypes[Uint64]:
		return Uint64
	}
	return Int64
}

// concatenate is _concatenate_chunks for one column: concat_compat casts the
// chunks to their common dtype. Booleans next to numbers become numbers, and
// the column an object column of them.
func concatenate(chunks []*column) *column {
	if len(chunks) == 1 {
		return chunks[0]
	}
	dtypes := map[Dtype]bool{}
	for _, chunk := range chunks {
		dtypes[chunk.dtype] = true
	}
	result := &column{dtype: commonDtype(dtypes)}
	for _, chunk := range chunks {
		switch result.dtype {
		case Object:
			result.values = append(result.values, chunk.object()...)
		case Float64:
			result.floats = append(result.floats, chunk.float()...)
		case BoolD:
			result.bools = append(result.bools, chunk.bools...)
		case Uint64:
			if chunk.dtype == BoolD {
				for _, value := range chunk.bools {
					result.uints = append(result.uints, map[bool]uint64{true: 1}[value])
				}
			} else {
				result.uints = append(result.uints, chunk.uints...)
			}
		default:
			if chunk.dtype == BoolD {
				for _, value := range chunk.bools {
					result.ints = append(result.ints, map[bool]int64{true: 1}[value])
				}
			} else {
				result.ints = append(result.ints, chunk.ints...)
			}
		}
	}
	if dtypes[BoolD] && result.dtype != BoolD && result.dtype != Object {
		// The numbers the booleans became are then boxed, not kept numeric.
		return &column{dtype: Object, values: result.object()}
	}
	return result
}

// textNames is _get_header's naming (names) and the columns dtype names as
// text: a renamed repeat takes the dtype of the name it repeats unless its
// new name has one, and later columns see that entry.
func textNames(header []string, textual func(column string) bool) ([]string, []bool) {
	columns := names(header)
	text := make([]bool, len(columns))
	if textual == nil {
		return columns, text
	}
	added := map[string]bool{}
	named := func(name string) bool { return added[name] || textual(name) }
	// The loop order of names: named columns, then the unnamed ones.
	order := make([]int, 0, len(header))
	for index, name := range header {
		if name != "" {
			order = append(order, index)
		}
	}
	for index, name := range header {
		if name == "" {
			order = append(order, index)
		}
	}
	for _, index := range order {
		original := header[index]
		if original == "" {
			original = "Unnamed: " + strconv.Itoa(index)
		}
		if columns[index] != original && named(original) {
			added[columns[index]] = true
		}
	}
	for index, name := range columns {
		text[index] = named(name)
	}
	return columns, text
}

// read is TextReader with low_memory=True and CParserWrapper.read.
func read(data []byte, decoded bool, textual func(column string) bool) (*Frame, error) {
	t := newTokenizer(data, decoded)
	// _get_header: the header line and the first data line.
	if err := t.tokenizeRows(2); err != nil {
		return nil, err
	}
	var header []string
	if t.lines > 0 {
		for index := 0; index < t.lineFields[0]; index++ {
			word := t.word(t.lineStart[0] + index)
			if !utf8.Valid(word) {
				return nil, ErrDecode
			}
			header = append(header, string(word))
		}
	}
	width, leading := len(header), 0
	if t.lines >= 2 {
		// More fields in the first data line than names: the first columns
		// are the index.
		width = t.lineFields[1]
		leading = width - len(header)
	}
	if width == 0 {
		return nil, ErrEmpty
	}
	columnNames, textColumn := textNames(header, textual)
	frame := &Frame{Columns: columnNames}
	// Rows are converted in chunks of buffer_lines; a column's dtype is
	// inferred per chunk.
	bufferLines := 1
	for heuristic := (1 << 20) / width; bufferLines*2 < heuristic; {
		bufferLines *= 2
	}
	chunks := make([][]*column, width)
	total := 0
	for {
		if buffered := t.lines - 1; buffered < bufferLines {
			if err := t.tokenizeRows(bufferLines - buffered); err != nil {
				return nil, err
			}
		}
		if t.lines <= 1 {
			break
		}
		rows := min(bufferLines, t.lines-1)
		words := make([][]byte, rows)
		for index := 0; index < width; index++ {
			for row := range words {
				words[row] = t.field(1+row, index)
			}
			var result *column
			var err error
			if index >= leading && textColumn[index-leading] {
				result, err = texts(words, true)
			} else {
				result, err = convert(words)
			}
			if err != nil {
				return nil, err
			}
			chunks[index] = append(chunks[index], result)
		}
		t.consume(rows)
		total += rows
	}
	if total == 0 {
		frame.Dtypes = make([]Dtype, len(header))
		for index := range frame.Dtypes {
			frame.Dtypes[index] = Object
		}
		return frame, nil
	}
	columns := make([]*column, 0, len(header))
	dtypes := map[Dtype]bool{}
	for _, parts := range chunks[leading:] {
		joined := concatenate(parts)
		columns = append(columns, joined)
		frame.Dtypes = append(frame.Dtypes, joined.dtype)
		dtypes[joined.dtype] = true
	}
	// iterrows(): each row is a Series of DataFrame.values, whose dtype is
	// find_common_type of the columns' (object for booleans next to numbers).
	rowDtype := commonDtype(dtypes)
	if len(dtypes) > 1 && dtypes[BoolD] {
		rowDtype = Object
	}
	numpy := rowDtype != Object
	cells := make([][]Value, len(columns))
	for index, joined := range columns {
		switch {
		case rowDtype == Float64:
			cells[index] = (&column{dtype: Float64, floats: joined.float()}).object()
		default:
			cells[index] = joined.object()
		}
	}
	frame.Rows = make([][]Value, total)
	flat := make([]Value, total*len(columns))
	for row := range frame.Rows {
		frame.Rows[row] = flat[row*len(columns) : (row+1)*len(columns)]
		for index := range columns {
			value := cells[index][row]
			value.NumPy = numpy
			frame.Rows[row][index] = value
		}
	}
	return frame, nil
}
