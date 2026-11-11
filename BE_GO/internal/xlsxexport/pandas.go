package xlsxexport

import (
	"math"
	"math/big"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// dtype is the pandas dtype of a DataFrame column.
type dtype uint8

const (
	dtObject   dtype = iota
	dtInt64          // int64
	dtFloat64        // float64 (None is NaN)
	dtBool           // bool
	dtNullable       // Int64 (None is <NA>)
)

// frameColumn is one DataFrame column: values keep their Python kinds; in a
// float64 column None stands for NaN, in an Int64 column for <NA>.
type frameColumn struct {
	dtype  dtype
	values []Value
}

// inferColumn is the DataFrame constructor's per-column inference
// (lib.maybe_convert_objects) for the kinds the exports hand it.
func inferColumn(values []Value) (frameColumn, error) {
	strings, nones, ints, floats, bools := 0, 0, 0, 0, 0
	for _, value := range values {
		switch value.Kind {
		case None:
			nones++
		case Int:
			ints++
		case Float:
			floats++
		case Bool:
			bools++
		default:
			strings++
		}
	}
	column := frameColumn{dtype: dtObject, values: values}
	switch {
	case strings > 0 || len(values) == 0 || nones == len(values):
	case bools > 0:
		if bools == len(values) {
			column.dtype = dtBool
		}
	case floats > 0 || nones > 0:
		column.dtype = dtFloat64
		converted := make([]Value, len(values))
		for i, value := range values {
			switch value.Kind {
			case Int:
				converted[i] = FloatValue(float64(value.Int))
			case None:
				converted[i] = FloatValue(math.NaN())
			default:
				converted[i] = value
			}
		}
		column.values = converted
	default:
		column.dtype = dtInt64
	}
	return column, nil
}

// isNA is pandas.isna of a scalar.
func isNA(value Value) bool {
	return value.Kind == None || (value.Kind == Float && math.IsNaN(value.Float))
}

// numericResult is lib.maybe_convert_numeric(values, set(), coerce_numeric=True).
type numericResult struct {
	dtype  dtype // dtFloat64, dtInt64 or dtBool
	values []Value
}

// toNumeric is pandas.to_numeric(series, errors='coerce') of a column.
func toNumeric(column frameColumn) (numericResult, error) {
	switch column.dtype {
	case dtInt64, dtFloat64, dtBool:
		return numericResult{dtype: column.dtype, values: column.values}, nil
	}
	var sawFloat, sawInt, sawBool bool
	floats := make([]float64, len(column.values))
	ints := make([]int64, len(column.values))
	for i, value := range column.values {
		switch value.Kind {
		case None:
			sawFloat = true
			floats[i] = math.NaN()
		case Float:
			sawFloat = true
			floats[i] = value.Float
		case Int:
			sawInt = true
			floats[i], ints[i] = float64(value.Int), value.Int
		case Bool:
			sawBool = true
			if value.Bool {
				floats[i], ints[i] = 1, 1
			}
		case Decimal:
			number, err := strconv.ParseFloat(value.Text, 64)
			if err != nil {
				return numericResult{}, ErrNotPorted
			}
			sawFloat = true
			floats[i] = number
		case Object:
			return numericResult{}, ErrNotPorted
		case Str:
			if value.Text == "" {
				sawFloat = true
				floats[i] = math.NaN()
				continue
			}
			number, maybeInt, ok, err := pandasFloatify(value.Text)
			if err != nil {
				return numericResult{}, err
			}
			if !ok {
				sawFloat = true
				floats[i] = math.NaN()
				continue
			}
			floats[i] = number
			if !maybeInt {
				sawFloat = true
				continue
			}
			whole, ok := pandasPythonInt(value.Text)
			if !ok {
				sawFloat = true // int(val) raises: coerced to NaN
				floats[i] = math.NaN()
				continue
			}
			switch {
			case whole.IsInt64():
				sawInt = true
				ints[i] = whole.Int64()
			case whole.Sign() > 0 && whole.BitLen() <= 64:
				return numericResult{}, ErrNotPorted // uint64 results
			default:
				sawFloat = true
			}
		}
	}
	result := numericResult{values: make([]Value, len(column.values))}
	if sawBool && !sawFloat && !sawInt {
		// maybe_convert_numeric: only bools seen (no int, float or null) gives a bool array.
		result.dtype = dtBool
		copy(result.values, column.values)
		return result, nil
	}
	if sawFloat || !sawInt {
		result.dtype = dtFloat64
		for i, number := range floats {
			result.values[i] = FloatValue(number)
		}
		return result, nil
	}
	result.dtype = dtInt64
	for i, number := range ints {
		result.values[i] = IntValue(number)
	}
	return result, nil
}

// pandasPythonInt is Python's int() of a string precise_xstrtod read as an
// integer (ASCII whitespace, sign, digits); a NUL byte makes int() raise.
func pandasPythonInt(text string) (*big.Int, bool) {
	if strings.IndexByte(text, 0) >= 0 {
		return nil, false
	}
	return new(big.Int).SetString(strings.TrimFunc(text, dto.PythonIsSpace), 10)
}

// pandasFloatify is pandas' floatify (pd_parser.c): precise_xstrtod over the
// UTF-8 bytes up to the first NUL, which must consume everything, else the
// spelled infinities; ok false is the ValueError.
func pandasFloatify(text string) (number float64, maybeInt bool, ok bool, err error) {
	if index := strings.IndexByte(text, 0); index >= 0 {
		text = text[:index]
	}
	number, end, failed, maybeInt, ambiguous := preciseXstrtod(text)
	if ambiguous {
		return 0, false, false, ErrNotPorted
	}
	if !failed && end == len(text) {
		return number, maybeInt, true, nil
	}
	lower := strings.ToLower(text)
	if strings.ContainsFunc(text, func(r rune) bool { return r >= utf8.RuneSelf }) {
		lower = asciiLower(text)
	}
	switch len(text) {
	case 3:
		if lower == "inf" {
			return math.Inf(1), false, true, nil
		}
	case 4:
		if lower == "-inf" {
			return math.Inf(-1), false, true, nil
		}
		if lower == "+inf" {
			return math.Inf(1), false, true, nil
		}
	case 8:
		if lower == "infinity" {
			return math.Inf(1), false, true, nil
		}
	case 9:
		if lower == "-infinity" {
			return math.Inf(-1), false, true, nil
		}
		if lower == "+infinity" {
			return math.Inf(1), false, true, nil
		}
	}
	return 0, false, false, nil
}

func asciiLower(text string) string {
	out := []byte(text)
	for i, b := range out {
		if b >= 'A' && b <= 'Z' {
			out[i] = b + 32
		}
	}
	return string(out)
}

var pandasPowers = func() [309]float64 {
	var table [309]float64
	for i := range table {
		table[i], _ = strconv.ParseFloat("1e"+strconv.Itoa(i), 64)
	}
	return table
}()

func xstrtodSpace(b byte) bool { return b == ' ' || (b >= '\t' && b <= '\r') }
func xstrtodDigit(b byte) bool { return b >= '0' && b <= '9' }

// preciseXstrtod is pandas' precise_xstrtod (tokenizer.c) with decimal '.',
// sci 'E', no thousands separator and skip_trailing: it returns the value, the
// end offset, whether it set ERANGE and maybe_int. The float operations are
// rounded one by one (explicit conversions stop Go from fusing them).
func preciseXstrtod(text string) (float64, int, bool, bool, bool) {
	fused, end, failed, maybeInt := preciseXstrtodWith(text, true)
	plain, _, plainFailed, _ := preciseXstrtodWith(text, false)
	if fused != plain || failed != plainFailed {
		// The digit accumulation number*10 + digit is contracted into a fused
		// multiply-add where the compiler does it (clang and GCC on arm64, so
		// the macOS and aarch64 pandas wheels) and not elsewhere (x86-64
		// wheels): beyond 2**53 the two differ, so the result depends on the
		// platform Django runs on.
		return 0, end, failed, maybeInt, true
	}
	return fused, end, failed, maybeInt, false
}

func preciseXstrtodWith(text string, fma bool) (float64, int, bool, bool) {
	accumulate := func(number float64, digit byte) float64 {
		if fma {
			return math.FMA(number, 10., float64(digit-'0'))
		}
		return float64(number*10.) + float64(digit-'0')
	}
	const maxDigits = 17
	at := func(p int) byte {
		if p < len(text) {
			return text[p]
		}
		return 0
	}
	maybeInt := true
	p := 0
	for xstrtodSpace(at(p)) {
		p++
	}
	negative := false
	switch at(p) {
	case '-':
		negative = true
		p++
	case '+':
		p++
	}
	number := 0.0
	exponent, numDigits, numDecimals := int32(0), 0, int32(0)
	for xstrtodDigit(at(p)) {
		if numDigits < maxDigits {
			number = accumulate(number, at(p))
			numDigits++
		} else {
			exponent++
		}
		p++
	}
	if at(p) == '.' {
		maybeInt = false
		p++
		for numDigits < maxDigits && xstrtodDigit(at(p)) {
			number = accumulate(number, at(p))
			p++
			numDigits++
			numDecimals++
		}
		if numDigits >= maxDigits {
			for xstrtodDigit(at(p)) {
				p++
			}
		}
		exponent -= numDecimals
	}
	if numDigits == 0 {
		return 0, p, true, maybeInt
	}
	if negative {
		number = -number
	}
	if b := at(p); b == 'E' || b == 'e' {
		maybeInt = false
		negative = false
		p++
		switch at(p) {
		case '-':
			negative = true
			p++
		case '+':
			p++
		}
		digits, n := 0, int32(0)
		for digits < maxDigits && xstrtodDigit(at(p)) {
			n = n*10 + int32(at(p)-'0')
			digits++
			p++
		}
		if negative {
			exponent -= n
		} else {
			exponent += n
		}
		if digits == 0 {
			p--
		}
	}
	failed := false
	switch {
	case exponent > 308:
		return math.Inf(1), p, true, maybeInt
	case exponent > 0:
		number = float64(number * pandasPowers[exponent])
	case exponent < -308:
		if exponent < -616 {
			number = 0.
		} else {
			number = float64(number / pandasPowers[-308-exponent])
			number = float64(number / pandasPowers[308])
		}
	default:
		number = float64(number / pandasPowers[-exponent])
	}
	if math.IsInf(number, 0) {
		failed = true
	}
	for xstrtodSpace(at(p)) {
		p++
	}
	return number, p, failed, maybeInt
}

// pythonStrOf is str() of a value in an object column (astype(str)).
func pythonStrOf(value Value) string {
	switch value.Kind {
	case None:
		return "None"
	case Int:
		return strconv.FormatInt(value.Int, 10)
	case Float:
		return pythonFloatRepr(value.Float)
	case Bool:
		if value.Bool {
			return "True"
		}
		return "False"
	}
	return value.Text
}

// pythonFloatRepr is repr(float) (numpy's str of a float64 agrees).
func pythonFloatRepr(number float64) string {
	switch {
	case math.IsNaN(number):
		return "nan"
	case math.IsInf(number, 1):
		return "inf"
	case math.IsInf(number, -1):
		return "-inf"
	}
	return dto.PythonFloatStr(number)
}

// percentG is C's "%.16g" (upper: "%.16G") of a finite float.
func percentG(number float64, upper bool) string {
	text := strconv.FormatFloat(number, 'g', 16, 64)
	if upper {
		text = strings.ToUpper(text)
	}
	return text
}

// pyLen is len() of a str (code points).
func pyLen(text string) int { return utf8.RuneCountInString(text) }
