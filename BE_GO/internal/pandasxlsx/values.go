package pandasxlsx

import (
	"math"
	"strconv"
	"time"
)

// Kind is the Python type of a value iterrows() yields, or of a column label
// columns.tolist() yields.
type Kind int

const (
	// NaN is float('nan'): an empty cell, an NA text or an error cell.
	NaN Kind = iota
	// String is a str.
	String
	// Int is a Python int that fits int64 (I).
	Int
	// Float is a float or a numpy.float64 (F): one str(), both JSON numbers.
	Float
	// Time is a pandas Timestamp or a datetime.datetime: both print as
	// 'YYYY-MM-DD HH:MM:SS[.ffffff]', are true and are not JSON serializable.
	Time
	// NaT is pandas.NaT: a missing value of a datetime64 or timedelta64
	// column or row.
	NaT
	// BigInt is a Python int outside int64; S holds its decimal digits.
	BigInt
	// Int64 is a numpy.int64 (I): a frame of int64 columns only.
	Int64
	// Uint64 is a numpy.uint64 (U): a frame of uint64 columns only.
	Uint64
	// Bool is a Python bool (B).
	Bool
	// NumpyBool is a numpy.bool_ (B): a frame of bool columns only.
	NumpyBool
	// TimeOfDay is a datetime.time; D is the time since midnight.
	TimeOfDay
	// Timedelta is a pandas Timedelta (D).
	Timedelta
	// PyTimedelta is a datetime.timedelta (D).
	PyTimedelta
	// Date is a datetime.date; T is its midnight.
	Date
)

// Value is one cell of a row, or one column label.
type Value struct {
	Kind Kind
	S    string
	I    int64
	F    float64
	T    time.Time
	U    uint64
	B    bool
	D    time.Duration
}

// TimeText is str() of a Time value.
func (v Value) TimeText() string {
	text := v.T.Format("2006-01-02 15:04:05")
	if micro := v.T.Nanosecond() / 1000; micro != 0 {
		text += "." + leftPad(strconv.Itoa(micro), 6)
	}
	return text
}

// Str is Python's str(value).
func (v Value) Str() string {
	switch v.Kind {
	case NaN:
		return "nan"
	case NaT:
		return "NaT"
	case String, BigInt:
		return v.S
	case Int, Int64:
		return strconv.FormatInt(v.I, 10)
	case Uint64:
		return strconv.FormatUint(v.U, 10)
	case Float:
		return floatRepr(v.F)
	case Bool, NumpyBool:
		if v.B {
			return "True"
		}
		return "False"
	case Time:
		return v.TimeText()
	case Date:
		return v.T.Format("2006-01-02")
	case TimeOfDay:
		hours, minutes, seconds, micro := clockParts(v.D)
		text := leftPad(strconv.FormatInt(hours, 10), 2) + ":" + leftPad(strconv.FormatInt(minutes, 10), 2) + ":" +
			leftPad(strconv.FormatInt(seconds, 10), 2)
		if micro != 0 {
			text += "." + leftPad(strconv.FormatInt(micro, 10), 6)
		}
		return text
	case Timedelta:
		// Timedelta.__str__: '<days> days[ +]HH:MM:SS[.ffffff]'.
		days, rest := floorDays(v.D)
		hours, minutes, seconds, micro := clockParts(rest)
		sign := " "
		if days < 0 {
			sign = " +"
		}
		text := strconv.FormatInt(days, 10) + " days" + sign + leftPad(strconv.FormatInt(hours, 10), 2) + ":" +
			leftPad(strconv.FormatInt(minutes, 10), 2) + ":" + leftPad(strconv.FormatInt(seconds, 10), 2)
		if micro != 0 {
			text += "." + leftPad(strconv.FormatInt(micro, 10), 6)
		}
		return text
	case PyTimedelta:
		// timedelta.__str__: '[<days> day[s], ]H:MM:SS[.ffffff]'.
		days, rest := floorDays(v.D)
		hours, minutes, seconds, micro := clockParts(rest)
		text := strconv.FormatInt(hours, 10) + ":" + leftPad(strconv.FormatInt(minutes, 10), 2) + ":" +
			leftPad(strconv.FormatInt(seconds, 10), 2)
		if days != 0 {
			unit := " days, "
			if days == 1 || days == -1 {
				unit = " day, "
			}
			text = strconv.FormatInt(days, 10) + unit + text
		}
		if micro != 0 {
			text += "." + leftPad(strconv.FormatInt(micro, 10), 6)
		}
		return text
	}
	return ""
}

// Truthy is Python's bool(value).
func (v Value) Truthy() bool {
	switch v.Kind {
	case String:
		return v.S != ""
	case Int, Int64:
		return v.I != 0
	case Uint64:
		return v.U != 0
	case Float:
		return v.F != 0 || math.IsNaN(v.F)
	case Bool, NumpyBool:
		return v.B
	case Timedelta, PyTimedelta:
		return v.D != 0
	}
	return true // nan, NaT, a big int, a datetime, a time, a date
}

// JSONSerializable reports whether json.dumps encodes the value: a str, an
// int, a float (nan and the infinities as NaN, Infinity and -Infinity) or a
// bool. The numpy integers and booleans, datetimes, times, dates, timedeltas
// and NaT raise TypeError.
func (v Value) JSONSerializable() bool {
	switch v.Kind {
	case NaN, String, Int, BigInt, Float, Bool:
		return true
	}
	return false
}

func leftPad(text string, width int) string {
	for len(text) < width {
		text = "0" + text
	}
	return text
}

// floorDays splits a duration into whole days (rounded down) and the rest.
func floorDays(d time.Duration) (int64, time.Duration) {
	const day = 24 * time.Hour
	days := int64(d / day)
	rest := d % day
	if rest < 0 {
		days--
		rest += day
	}
	return days, rest
}

func clockParts(d time.Duration) (hours, minutes, seconds, micro int64) {
	total := int64(d / time.Microsecond)
	micro = total % 1000000
	total /= 1000000
	return total / 3600, total / 60 % 60, total % 60, micro
}
