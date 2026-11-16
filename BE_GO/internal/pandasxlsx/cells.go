package pandasxlsx

import (
	"math"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"time"

	"golang.org/x/text/cases"
	"golang.org/x/text/language"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

var (
	// openpyxl's ISO_REGEX and ISO_DURATION, matched at the start of the text.
	// Python's \d is every decimal digit (the category Nd).
	isoMoment   = regexp.MustCompile(`^((\p{Nd}{4})-(\p{Nd}{2})-(\p{Nd}{2}))?T?((\p{Nd}{2}):(\p{Nd}{2})(:(\p{Nd}{2})(\.\p{Nd}{1,3})?)?)?Z?`)
	isoDuration = regexp.MustCompile(`^PT((\p{Nd}+)H)?((\p{Nd}+)M)?((\p{Nd}+(\.\p{Nd}{1,3})?)S)?`)
)

// microsecondsPerDay is one day in datetime.timedelta's unit.
const microsecondsPerDay = 86400 * 1000000

// datetime.timedelta's limits: a day count in [-999999999, 999999999]
// (from_excel's constructor raises outside it) and the same duration in whole
// microseconds, which is the range pandas holds as timedelta64[ns]
// (Timedelta.max is 106751 days 23:47:16.854775807).
var (
	maxDeltaDays = big.NewInt(999999999)
	minDeltaDays = big.NewInt(-999999999)
	maxDelta     = mustInt("86399999999999999999")
	minDelta     = mustInt("-86399999999999999999")
	maxTimedelta = mustInt("9223372036854775")
	minTimedelta = mustInt("-9223372036854775")
)

func mustInt(text string) *big.Int {
	value, _ := new(big.Int).SetString(text, 10)
	return value
}

var (
	windowsEpoch = time.Date(1899, 12, 30, 0, 0, 0, 0, time.UTC)
	macEpoch     = time.Date(1904, 1, 1, 0, 0, 0, 0, time.UTC)
	datetimeMin  = time.Date(1, 1, 1, 0, 0, 0, 0, time.UTC)
	datetimeMax  = time.Date(9999, 12, 31, 23, 59, 59, 999999000, time.UTC)
)

// cellColumn is openpyxl's coordinate_to_tuple and column_index_from_string for
// a cell reference: the column before the first ASCII digit (the row after it
// only has to be an int), uppercased, three characters at most, and within the
// 18278 columns openpyxl numbers (Excel writes at most 16384).
func cellColumn(reference string) (int64, error) {
	runes := []rune(reference)
	index := len(runes) - 1 // coordinate_to_tuple leaves idx at the last character
	for position, r := range runes {
		if r >= '0' && r <= '9' {
			index = position
			break
		}
	}
	if index < 0 {
		return 0, ErrInvalid
	}
	if _, ok := pythonInt(string(runes[index:])); !ok {
		return 0, ErrInvalid
	}
	letters := runes[:index]
	if len(letters) == 0 || len(letters) > 3 {
		return 0, ErrInvalid
	}
	// column_index_from_string reads the last three letters of str.upper().
	upper := []rune(cases.Upper(language.Und).String(string(letters)))
	column, power := 0, 1
	for at := len(upper) - 1; at >= 0 && at >= len(upper)-3; at-- {
		letter := upper[at]
		if letter < 'A' || letter > 'Z' {
			return 0, ErrInvalid
		}
		column += (int(letter-'A') + 1) * power
		power *= 26
	}
	if column < 1 || column > 18278 {
		return 0, ErrInvalid
	}
	return int64(column), nil
}

// rowCells is parse_row and _get_row: the cells of a row from column 1 to its
// last cell, each as pandas' _convert_cell returns it. openpyxl parses every
// child of a row as a cell.
func rowCells(row sheetRow, context sheetContext) ([]cell, error) {
	type placed struct {
		column int64
		value  cell
	}
	var cells []placed
	counter := int64(0)
	for _, item := range row.Cells {
		counter++ // a cell without a coordinate follows the last one
		if item.Reference != "" {
			column, err := cellColumn(item.Reference)
			if err != nil {
				return nil, err
			}
			counter = column
		}
		kind := "n"
		if item.Type != nil {
			kind = *item.Type
		}
		// A style is a cell format index; one that is no index is no date format.
		style := int64(-1)
		if item.Style != nil && *item.Style != "" {
			index, ok := pythonInt(*item.Style)
			if !ok || !index.IsInt64() {
				return nil, ErrInvalid
			}
			style = index.Int64()
		}
		value := cell{}
		raw := ""
		if len(item.Values) > 0 && kind != "inlineStr" {
			raw = item.Values[0]
		}
		var err error
		switch {
		case kind == "inlineStr":
			if len(item.Inline) > 0 {
				value = cell{kind: cellString, s: item.Inline[0].content()}
			}
		case raw == "":
			// No value: an empty cell whatever its type.
		case kind == "n":
			if value, err = context.number(raw, style); err != nil {
				return nil, err
			}
		case kind == "s":
			index, ok := pythonInt(raw)
			if !ok || !index.IsInt64() {
				return nil, ErrInvalid
			}
			at := index.Int64()
			if at < 0 {
				at += int64(len(context.shared))
			}
			if at < 0 || at >= int64(len(context.shared)) {
				return nil, ErrInvalid
			}
			value = cell{kind: cellString, s: context.shared[at]}
		case kind == "b":
			// bool(int(value))
			number, ok := pythonInt(raw)
			if !ok {
				return nil, ErrInvalid
			}
			value = cell{kind: cellBool, b: number.Sign() != 0}
		case kind == "e":
			value = cell{kind: cellNaN}
		case kind == "d":
			if value, err = isoCell(raw); err != nil {
				return nil, err
			}
		default:
			// 'str' (a formula's text) and any other type: the text as written.
			value = cell{kind: cellString, s: raw}
		}
		cells = append(cells, placed{column: counter, value: value})
	}
	if len(cells) == 0 {
		return nil, nil
	}
	// The row is as wide as its last cell's column.
	width := cells[len(cells)-1].column
	if width < 1 {
		// Every column is at least 1: a coordinate's column is, and a cell
		// without one adds to the last.
		return nil, refusal.AppState("pandasxlsx: a row cell before the first column")
	}
	out := make([]cell, int(width))
	for _, item := range cells {
		if item.column >= 1 && item.column <= width {
			out[item.column-1] = item.value
		}
	}
	return out, nil
}

// number is openpyxl's _cast_number (an int unless written with '.', 'e' or
// 'E'), from_excel for a date or timedelta format, and pandas'
// _convert_cell for a plain number: an integral float is an int.
func (context sheetContext) number(raw string, style int64) (cell, error) {
	styleKind := 0
	if style >= 0 && style < int64(len(context.styleKinds)) {
		styleKind = context.styleKinds[style]
	}
	var whole *big.Int
	value, infinite := 0.0, false
	if strings.ContainsAny(raw, ".eE") {
		number, ok := pythonFloat(raw)
		if !ok {
			return cell{}, ErrInvalid // float(text) raises
		}
		value, infinite = number, math.IsInf(number, 0)
	} else {
		number, ok := pythonInt(raw)
		if !ok {
			return cell{}, ErrInvalid // int(text) raises
		}
		whole = number
	}
	switch {
	case styleKind != 0 && infinite:
		return cell{kind: cellNaN}, nil // OverflowError or ValueError: '#VALUE!'
	case styleKind == 2:
		return excelDelta(whole, value)
	case styleKind == 1:
		return context.excelMoment(whole, value), nil
	case infinite:
		return cell{}, ErrInvalid // _convert_cell's int(inf) raises OverflowError
	case whole != nil:
		return intCell(whole), nil
	case value == math.Trunc(value):
		whole, _ = new(big.Float).SetFloat64(value).Int(nil)
		return intCell(whole), nil
	}
	return cell{kind: cellFloat, f: value}, nil
}

// excelMoment is from_excel for a date format: a time of day for a serial
// from 0 up to 1, else the epoch plus the days and the day fraction rounded
// to milliseconds, with the 1900 leap day shift below serial 60 in the 1900
// system. A serial outside datetime's range is an error cell (nan).
func (context sheetContext) excelMoment(whole *big.Int, value float64) cell {
	day, milliseconds := 0.0, int64(0)
	if whole != nil {
		if !whole.IsInt64() || whole.Int64() > 10000000 || whole.Int64() < -10000000 {
			return cell{kind: cellNaN}
		}
		value = float64(whole.Int64())
		day = value
	} else {
		// divmod(value, 1) as CPython computes it.
		fraction := math.Mod(value, 1)
		day = value - fraction
		if fraction != 0 {
			if fraction < 0 {
				fraction++
				day--
			}
		} else {
			fraction = 0
		}
		day = math.Floor(day)
		milliseconds = int64(math.RoundToEven(float64(fraction*86400) * 1000))
	}
	if value >= 0 && value < 1 && milliseconds < 86400000 {
		return cell{kind: cellClock, d: time.Duration(milliseconds) * time.Millisecond}
	}
	if value > 0 && value < 60 && !context.mac {
		day++
	}
	if math.Abs(day) > 10000000 {
		return cell{kind: cellNaN}
	}
	epoch := windowsEpoch
	if context.mac {
		epoch = macEpoch
	}
	moment := epoch.AddDate(0, 0, int(day))
	if moment.Before(datetimeMin) || moment.After(datetimeMax) {
		return cell{kind: cellNaN}
	}
	if moment = moment.Add(time.Duration(milliseconds) * time.Millisecond); moment.After(datetimeMax) {
		return cell{kind: cellNaN}
	}
	return cell{kind: cellTime, t: moment}
}

// deltaAccumulator is datetime.timedelta's constructor arithmetic: exact
// microseconds plus the fractions of float arguments, rounded half to even
// at the end.
type deltaAccumulator struct {
	microseconds big.Int
	leftover     float64
}

// add is accum() for a float argument of the given unit.
func (a *deltaAccumulator) add(number float64, unit int64) {
	whole, fraction := math.Modf(number)
	if whole != 0 {
		part, _ := new(big.Float).SetFloat64(whole).Int(nil)
		a.microseconds.Add(&a.microseconds, part.Mul(part, big.NewInt(unit)))
	}
	if fraction == 0 {
		return
	}
	whole, fraction = math.Modf(float64(unit) * fraction)
	a.microseconds.Add(&a.microseconds, big.NewInt(int64(whole)))
	a.leftover += fraction
}

func (a *deltaAccumulator) total() *big.Int {
	total := new(big.Int).Set(&a.microseconds)
	if a.leftover == 0 {
		return total
	}
	rounded := math.Round(a.leftover)
	if math.Abs(rounded-a.leftover) == 0.5 {
		odd := float64(a.microseconds.Bit(0))
		rounded = float64(2.0*math.Round((a.leftover+odd)*0.5)) - odd
	}
	return total.Add(total, big.NewInt(int64(rounded)))
}

// excelDuration is timedelta(days=value) (timedelta(days=whole) for an integer
// cell) in microseconds, and whether datetime.timedelta raises on it.
func excelDuration(whole *big.Int, value float64) (*big.Int, bool) {
	if whole != nil {
		if whole.Cmp(maxDeltaDays) > 0 || whole.Cmp(minDeltaDays) < 0 {
			return nil, true // timedelta(days=...) raises OverflowError
		}
		return new(big.Int).Mul(whole, big.NewInt(microsecondsPerDay)), false
	}
	if !(value > -1e9 && value < 1e9) {
		return nil, true // an infinity, or a day count past timedelta's last day
	}
	accumulator := deltaAccumulator{}
	accumulator.add(value, microsecondsPerDay)
	total := accumulator.total()
	if total.Cmp(maxDelta) > 0 || total.Cmp(minDelta) < 0 {
		return nil, true // the rounded fraction reached another day
	}
	return total, false
}

// roundMilliseconds is from_excel's rounding of a timedelta to milliseconds:
// timedelta(seconds=td.total_seconds() // 1, microseconds=round(td.microseconds, -3)).
func roundMilliseconds(total *big.Int) *big.Int {
	micro := new(big.Int).Mod(total, big.NewInt(1000000))
	if micro.Sign() == 0 {
		return total
	}
	seconds := new(big.Int).Sub(total, micro)
	seconds.Quo(seconds, big.NewInt(1000000))
	thousands := new(big.Int).Quo(micro, big.NewInt(1000))
	rest := new(big.Int).Rem(micro, big.NewInt(1000))
	switch half := big.NewInt(500); {
	case rest.Cmp(half) > 0:
		thousands.Add(thousands, big.NewInt(1))
	case rest.Cmp(half) == 0 && thousands.Bit(0) == 1:
		thousands.Add(thousands, big.NewInt(1))
	}
	seconds.Mul(seconds, big.NewInt(1000000))
	return seconds.Add(seconds, thousands.Mul(thousands, big.NewInt(1000)))
}

// deltaCell is a duration as pandas holds it: a timedelta within Timedelta's
// range (datetime.timedelta as openpyxl reads it, carried as
// timedelta64[ns]), and a value past it as the Python datetime.timedelta
// object pandas keeps, whose total microseconds Micro carries.
func deltaCell(total *big.Int) (cell, error) {
	if total.Cmp(maxTimedelta) > 0 || total.Cmp(minTimedelta) < 0 {
		return cell{kind: cellDelta, micro: total}, nil
	}
	return cell{kind: cellDelta, d: time.Duration(total.Int64()) * time.Microsecond}, nil
}

// excelDelta is from_excel for a timedelta format: timedelta(days=value)
// rounded to milliseconds. A value datetime.timedelta raises on is an error
// cell: openpyxl answers '#VALUE!' for it.
func excelDelta(whole *big.Int, value float64) (cell, error) {
	total, overflow := excelDuration(whole, value)
	if overflow {
		return cell{kind: cellNaN}, nil
	}
	return deltaCell(roundMilliseconds(total))
}

// isoCell is from_ISO8601 for a 't="d"' cell: a date, a time, a datetime or
// a duration. Text it raises on is ErrInvalid.
func isoCell(raw string) (cell, error) {
	number := func(text string) int {
		digits, _ := digitText(text)
		value, _ := strconv.Atoi(digits)
		return value
	}
	if match := isoMoment.FindStringSubmatch(raw); match != nil && match[0] != "" && (match[1] != "" || match[5] != "") {
		year, month, day := number(match[2]), number(match[3]), number(match[4])
		hour, minute, second, micro := number(match[6]), number(match[7]), number(match[9]), 0
		if match[10] != "" {
			digits, _ := digitText(match[10])
			fraction, _ := strconv.ParseFloat(digits, 64)
			micro = int(float64(fraction * 1000000)) // int(float(text) * 1_000_000)
		}
		if hour > 23 || minute > 59 || second > 59 || micro > 999999 {
			return cell{}, ErrInvalid
		}
		clock := time.Duration(hour)*time.Hour + time.Duration(minute)*time.Minute + time.Duration(second)*time.Second +
			time.Duration(micro)*time.Microsecond
		if match[1] == "" {
			return cell{kind: cellClock, d: clock}, nil
		}
		date := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
		if year < 1 || date.Year() != year || int(date.Month()) != month || date.Day() != day {
			return cell{}, ErrInvalid
		}
		if match[5] == "" {
			return cell{kind: cellCalendar, t: date}, nil
		}
		return cell{kind: cellTime, t: date.Add(clock)}, nil
	}
	match := isoDuration.FindStringSubmatch(raw)
	if match == nil || match[1] == "" && match[3] == "" && match[5] == "" {
		return cell{}, ErrInvalid // from_ISO8601 raises ValueError for the text
	}
	// timedelta(hours=float, minutes=float, seconds=float)
	accumulator := deltaAccumulator{}
	for _, part := range []struct {
		text string
		unit int64
	}{{match[6], 1000000}, {match[4], 60 * 1000000}, {match[2], 3600 * 1000000}} {
		if part.text == "" {
			continue
		}
		digits, _ := digitText(part.text)
		value, _ := strconv.ParseFloat(digits, 64)
		if math.IsInf(value, 0) {
			return cell{}, ErrInvalid // timedelta(hours=inf) raises OverflowError
		}
		accumulator.add(value, part.unit)
	}
	total := accumulator.total()
	if total.Cmp(maxDelta) > 0 || total.Cmp(minDelta) < 0 {
		return cell{}, ErrInvalid // datetime.timedelta raises OverflowError
	}
	return deltaCell(total)
}
