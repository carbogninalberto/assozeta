package pandasxlsx

import (
	"encoding/json"
	"math"
	"math/big"
	"strconv"
	"strings"
)

// IndexClass is the Index pandas builds from the column labels: it decides
// which keys `key in row.keys()` finds and what row[key] returns.
type IndexClass int

const (
	// ObjectIndex holds the labels as Python objects (text, or a mix).
	ObjectIndex IndexClass = iota
	// IntIndex is an int64 Index: whole numbers only.
	IntIndex
	// FloatIndex is a float64 Index: numbers with a decimal or an error cell.
	FloatIndex
	// BoolIndex is a bool Index: booleans only.
	BoolIndex
	// DatetimeIndex holds datetimes (and NaT).
	DatetimeIndex
	// TimedeltaIndex holds durations (and NaT).
	TimedeltaIndex
	// Uint64Index is a uint64 Index: whole numbers in [0, 2**64) that do
	// not all fit int64.
	Uint64Index
)

// Lookup is `key in row.keys()` and the position row[key] reads, for a key
// of a JSON document: a string, a bool or a json.Number (any other value is
// no label). found is false where the key is no label. raises is true where
// row[key] raises although the key is a label (a whole number read as a
// position past the row's end). ErrUnsupported: text looked up in a
// DatetimeIndex or a TimedeltaIndex, which pandas parses as a date or a
// duration.
func (f *Frame) Lookup(key any) (position int, found, raises bool, err error) {
	switch f.Index {
	case DatetimeIndex, TimedeltaIndex:
		if _, text := key.(string); text {
			return 0, false, false, ErrUnsupported
		}
		return 0, false, false, nil
	case BoolIndex:
		// The engine takes booleans only.
		flag, isBool := key.(bool)
		if !isBool {
			return 0, false, false, nil
		}
		for index, label := range f.Labels {
			if label.B == flag {
				return index, true, false, nil
			}
		}
		return 0, false, false, nil
	case IntIndex, FloatIndex, Uint64Index:
		// The engine takes whole numbers and floats, never booleans; an
		// int64 Index only floats without a fraction, a uint64 Index only
		// whole numbers in [0, 2**64).
		number, isNumber := key.(json.Number)
		if !isNumber {
			return 0, false, false, nil
		}
		whole, value, isWhole := jsonNumber(number)
		if f.Index == Uint64Index {
			if !isWhole {
				if math.IsNaN(value) || math.IsInf(value, 0) || value != math.Trunc(value) {
					return 0, false, false, nil
				}
				whole, _ = new(big.Float).SetFloat64(value).Int(nil)
			}
			for index, label := range f.Labels {
				if labelWhole := labelInteger(label); labelWhole != nil && labelWhole.Cmp(whole) == 0 {
					return index, true, false, nil
				}
			}
			return 0, false, false, nil
		}
		if f.Index == IntIndex {
			if !isWhole {
				if value != math.Trunc(value) || math.IsInf(value, 0) {
					return 0, false, false, nil
				}
				whole, _ = new(big.Float).SetFloat64(value).Int(nil)
			}
			if !whole.IsInt64() {
				return 0, false, false, nil
			}
			for index, label := range f.Labels {
				if label.I == whole.Int64() {
					return index, true, false, nil
				}
			}
			return 0, false, false, nil
		}
		if isWhole {
			value, _ = new(big.Float).SetInt(whole).Float64()
		}
		for index, label := range f.Labels {
			if label.Kind == Float && label.F == value {
				return index, true, false, nil
			}
		}
		return 0, false, false, nil
	}
	// An object Index: the label equal to the key (True is 1, False is 0).
	match := -1
	switch typed := key.(type) {
	case string:
		for index, label := range f.Labels {
			if label.Kind == String && label.S == typed {
				return index, true, false, nil
			}
		}
		return 0, false, false, nil
	case bool:
		number := 0.0
		if typed {
			number = 1
		}
		match = f.numberLabel(nil, number)
	case json.Number:
		whole, value, isWhole := jsonNumber(typed)
		if isWhole {
			match = f.numberLabel(whole, 0)
			if match >= 0 && !f.intLabels() {
				// Series.__getitem__ reads a whole number as a position
				// unless the Index infers as 'integer' or 'mixed-integer'.
				if !whole.IsInt64() || whole.Int64() >= int64(len(f.Labels)) || whole.Int64() < -int64(len(f.Labels)) {
					return 0, true, true, nil
				}
				position = int(whole.Int64())
				if position < 0 {
					position += len(f.Labels)
				}
				return position, true, false, nil
			}
		} else {
			match = f.numberLabel(nil, value)
		}
	}
	if match < 0 {
		return 0, false, false, nil
	}
	return match, true, false, nil
}

// jsonNumber is json.loads' number: an int unless written with '.', 'e' or
// 'E'.
func jsonNumber(number json.Number) (whole *big.Int, value float64, isWhole bool) {
	text := string(number)
	if !strings.ContainsAny(text, ".eE") {
		if whole, ok := new(big.Int).SetString(text, 10); ok {
			return whole, 0, true
		}
	}
	value, _ = strconv.ParseFloat(text, 64)
	return nil, value, false
}

// numberLabel is the first label equal to a number: whole (when set) or
// value.
func (f *Frame) numberLabel(whole *big.Int, value float64) int {
	for index, label := range f.Labels {
		switch label.Kind {
		case Int, Bool, Float, BigInt:
		default:
			continue
		}
		number := label.F
		switch label.Kind {
		case Int:
			if whole != nil {
				if whole.IsInt64() && whole.Int64() == label.I {
					return index
				}
				continue
			}
			if value == math.Trunc(value) && !math.IsInf(value, 0) {
				exact, _ := new(big.Float).SetFloat64(value).Int(nil)
				if exact.IsInt64() && exact.Int64() == label.I {
					return index
				}
			}
			continue
		case BigInt:
			// A Python int outside int64: its digits are in S.
			bigValue, ok := new(big.Int).SetString(label.S, 10)
			if !ok {
				continue
			}
			if whole != nil {
				if bigValue.Cmp(whole) == 0 {
					return index
				}
				continue
			}
			if value == math.Trunc(value) && !math.IsNaN(value) && !math.IsInf(value, 0) {
				exact, _ := new(big.Float).SetFloat64(value).Int(nil)
				if exact.Cmp(bigValue) == 0 {
					return index
				}
			}
			continue
		case Bool:
			number = 0
			if label.B {
				number = 1
			}
		}
		if whole != nil {
			exact, accuracy := new(big.Float).SetFloat64(number).Int(nil)
			if !math.IsNaN(number) && !math.IsInf(number, 0) && accuracy == big.Exact && exact.Cmp(whole) == 0 {
				return index
			}
			continue
		}
		if number == value {
			return index
		}
	}
	return -1
}

// labelInteger is the whole number a label of a uint64 Index holds.
func labelInteger(label Value) *big.Int {
	switch label.Kind {
	case Int:
		return big.NewInt(label.I)
	case BigInt:
		if value, ok := new(big.Int).SetString(label.S, 10); ok {
			return value
		}
	case Uint64:
		return new(big.Int).SetUint64(label.U)
	}
	return nil
}

// intLabels reports whether Series.__getitem__ reads a whole-number key of an
// object Index as a label: it does when the Index infers as 'integer' (whole
// numbers only) or 'mixed-integer' (whole numbers with text, booleans, dates
// or durations). An Index inferring as 'mixed-integer-float' (whole numbers
// with floats or nan) reads it as a position, and so does one without a whole
// number.
func (f *Frame) intLabels() bool {
	whole, floats, other := false, false, false
	for _, label := range f.Labels {
		switch label.Kind {
		case Int, BigInt:
			whole = true
		case Float, NaN:
			floats = true
		default:
			other = true
		}
	}
	return whole && (other || !floats)
}
