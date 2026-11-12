package pandasxlsx

import (
	"math"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"time"
)

var (
	integerText = regexp.MustCompile(`^[+-]?[0-9]+$`)
	floatText   = regexp.MustCompile(`^[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?$`)
	// openpyxl's ISO_REGEX and ISO_DURATION, matched at the start of the text.
	isoMoment   = regexp.MustCompile(`^(([0-9]{4})-([0-9]{2})-([0-9]{2}))?T?(([0-9]{2}):([0-9]{2})(:([0-9]{2})(\.[0-9]{1,3})?)?)?Z?`)
	isoDuration = regexp.MustCompile(`^PT(([0-9]+)H)?(([0-9]+)M)?(([0-9]+(\.[0-9]{1,3})?)S)?`)
)

// maxDeltaDays bounds the durations read: far inside pandas' Timedelta range
// and exact in float seconds.
const maxDeltaDays = 100000

const microsecondsPerDay = 86400 * 1000000

var (
	windowsEpoch = time.Date(1899, 12, 30, 0, 0, 0, 0, time.UTC)
	macEpoch     = time.Date(1904, 1, 1, 0, 0, 0, 0, time.UTC)
	datetimeMin  = time.Date(1, 1, 1, 0, 0, 0, 0, time.UTC)
	datetimeMax  = time.Date(9999, 12, 31, 23, 59, 59, 999999000, time.UTC)
)

// rowCells is parse_row and _get_row: the cells of a row from column 1 to its
// last cell, each as pandas' _convert_cell returns it.
func rowCells(row sheetRow, context sheetContext) ([]cell, error) {
	type placed struct {
		column int
		value  cell
	}
	var cells []placed
	counter := 0
	for _, item := range row.Cells {
		if item.XMLName.Space != mainNS || item.XMLName.Local != "c" || len(item.Values) > 1 || len(item.Inline) > 1 {
			return nil, ErrUnsupported
		}
		if item.Reference != "" {
			column, ok := columnIndex(item.Reference)
			if !ok {
				return nil, ErrUnsupported
			}
			counter = column
		} else {
			counter++
		}
		kind := "n"
		if item.Type != nil {
			kind = *item.Type
		}
		// A style that is no cell format index is no date format.
		style := 0
		if item.Style != nil {
			style = -1
			if *item.Style != "" {
				if !integerText.MatchString(*item.Style) || len(*item.Style) > 9 {
					return nil, ErrUnsupported
				}
				style, _ = strconv.Atoi(*item.Style)
			}
		}
		value := cell{}
		raw := ""
		if len(item.Values) == 1 && kind != "inlineStr" {
			raw = item.Values[0]
		}
		var err error
		switch {
		case kind == "inlineStr":
			if len(item.Inline) == 1 {
				value = cell{kind: cellString, s: item.Inline[0].content()}
			}
		case raw == "":
			// No value: an empty cell whatever its type.
		case kind == "n":
			if value, err = context.number(raw, style); err != nil {
				return nil, err
			}
		case kind == "s":
			index, err := strconv.Atoi(raw)
			if err != nil || index < 0 || index >= len(context.shared) {
				return nil, ErrUnsupported
			}
			value = cell{kind: cellString, s: context.shared[index]}
		case kind == "b":
			// bool(int(value))
			if !integerText.MatchString(raw) {
				return nil, ErrUnsupported
			}
			value = cell{kind: cellBool, b: strings.Trim(raw, "+-0") != ""}
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
		return nil, ErrUnsupported
	}
	out := make([]cell, width)
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
func (context sheetContext) number(raw string, style int) (cell, error) {
	styleKind := 0
	if style >= 0 && style < len(context.styleKinds) {
		styleKind = context.styleKinds[style]
	}
	var whole *big.Int
	value, infinite := 0.0, false
	if strings.ContainsAny(raw, ".eE") {
		if !floatText.MatchString(raw) {
			return cell{}, ErrUnsupported
		}
		var err error
		if value, err = strconv.ParseFloat(raw, 64); err != nil {
			if !math.IsInf(value, 0) {
				return cell{}, ErrUnsupported
			}
			infinite = true
		}
	} else {
		if !integerText.MatchString(raw) || len(raw) > 300 {
			return cell{}, ErrUnsupported
		}
		whole, _ = new(big.Int).SetString(strings.TrimPrefix(raw, "+"), 10)
	}
	switch {
	case styleKind != 0 && infinite:
		return cell{kind: cellNaN}, nil // OverflowError or ValueError: '#VALUE!'
	case styleKind == 2:
		return excelDelta(whole, value)
	case styleKind == 1:
		return context.excelMoment(whole, value), nil
	case infinite:
		return cell{}, ErrUnsupported // int(inf) raises
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
	microseconds int64
	leftover     float64
}

// add is accum() for a float argument of the given unit.
func (a *deltaAccumulator) add(number float64, unit int64) {
	whole, fraction := math.Modf(number)
	a.microseconds += int64(whole) * unit
	if fraction == 0 {
		return
	}
	whole, fraction = math.Modf(float64(unit) * fraction)
	a.microseconds += int64(whole)
	a.leftover += fraction
}

func (a *deltaAccumulator) total() int64 {
	if a.leftover == 0 {
		return a.microseconds
	}
	rounded := math.Round(a.leftover)
	if math.Abs(rounded-a.leftover) == 0.5 {
		odd := float64(a.microseconds & 1)
		rounded = float64(2.0*math.Round((a.leftover+odd)*0.5)) - odd
	}
	return a.microseconds + int64(rounded)
}

// excelDelta is from_excel for a timedelta format: timedelta(days=value)
// rounded to milliseconds.
func excelDelta(whole *big.Int, value float64) (cell, error) {
	var total int64
	if whole != nil {
		if !whole.IsInt64() || whole.Int64() >= maxDeltaDays || whole.Int64() <= -maxDeltaDays {
			return cell{}, ErrUnsupported
		}
		total = whole.Int64() * microsecondsPerDay
	} else {
		if math.Abs(value) >= maxDeltaDays {
			return cell{}, ErrUnsupported
		}
		accumulator := deltaAccumulator{}
		accumulator.add(value, microsecondsPerDay)
		total = accumulator.total()
	}
	if micro := floorMod(total, 1000000); micro != 0 {
		// timedelta(seconds=td.total_seconds() // 1, microseconds=round(td.microseconds, -3))
		seconds := int64(math.Floor(float64(total) / 1000000))
		thousands, rest := micro/1000, micro%1000
		if rest > 500 || rest == 500 && thousands%2 == 1 {
			thousands++
		}
		total = seconds*1000000 + thousands*1000
	}
	return cell{kind: cellDelta, d: time.Duration(total) * time.Microsecond}, nil
}

func floorMod(value, modulus int64) int64 {
	rest := value % modulus
	if rest < 0 {
		rest += modulus
	}
	return rest
}

// isoCell is from_ISO8601 for a 't="d"' cell: a date, a time, a datetime or
// a duration. Text it raises on is ErrUnsupported.
func isoCell(raw string) (cell, error) {
	if !isASCII(raw) {
		return cell{}, ErrUnsupported
	}
	number := func(text string) int {
		value, _ := strconv.Atoi(text)
		return value
	}
	if match := isoMoment.FindStringSubmatch(raw); match != nil && match[0] != "" && (match[1] != "" || match[5] != "") {
		year, month, day := number(match[2]), number(match[3]), number(match[4])
		hour, minute, second, micro := number(match[6]), number(match[7]), number(match[9]), 0
		if match[10] != "" {
			fraction, _ := strconv.ParseFloat(match[10], 64)
			micro = int(float64(fraction * 1000000)) // int(float(text) * 1_000_000)
		}
		if hour > 23 || minute > 59 || second > 59 || micro > 999999 {
			return cell{}, ErrUnsupported
		}
		clock := time.Duration(hour)*time.Hour + time.Duration(minute)*time.Minute + time.Duration(second)*time.Second +
			time.Duration(micro)*time.Microsecond
		if match[1] == "" {
			return cell{kind: cellClock, d: clock}, nil
		}
		date := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
		if year < 1 || date.Year() != year || int(date.Month()) != month || date.Day() != day {
			return cell{}, ErrUnsupported
		}
		if match[5] == "" {
			return cell{kind: cellCalendar, t: date}, nil
		}
		return cell{kind: cellTime, t: date.Add(clock)}, nil
	}
	match := isoDuration.FindStringSubmatch(raw)
	if match == nil || match[1] == "" && match[3] == "" && match[5] == "" {
		return cell{}, ErrUnsupported
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
		if len(part.text) > 12 {
			return cell{}, ErrUnsupported
		}
		value, _ := strconv.ParseFloat(part.text, 64)
		if value >= maxDeltaDays*24 {
			return cell{}, ErrUnsupported
		}
		accumulator.add(value, part.unit)
	}
	total := accumulator.total()
	if total >= maxDeltaDays*microsecondsPerDay {
		return cell{}, ErrUnsupported
	}
	return cell{kind: cellDelta, d: time.Duration(total) * time.Microsecond}, nil
}
