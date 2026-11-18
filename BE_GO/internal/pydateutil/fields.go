package pydateutil

import (
	"math/big"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
)

// Fields is what dateutil's parser._parse sets for a text, and the datetime
// pandas' dateutil_parse builds from it: the fields the text itself writes
// (nil where it leaves one out) and default.replace(**those fields) with
// pandas' _DEFAULT_DATETIME, datetime(1, 1, 1), as Result. It is the raw
// material of pandas' parse_datetime_string_with_reso, which derives a text's
// resolution from which of these attributes are set rather than from the text.
//
// The pointers are the parser's own values: they are never written after
// ParseFields returns.
type Fields struct {
	Year, Month, Day     *big.Int
	Hour, Minute, Second *big.Int
	// Microsecond is parser._result.microsecond, set only when the text
	// writes a fraction of a second (HasMicrosecond).
	Microsecond    int
	HasMicrosecond bool
	// Weekday is parser._result.weekday (0 Monday .. 6 Sunday), -1 when the
	// text names none.
	Weekday int
	// Result is pandas' datetime(1, 1, 1).replace(**the fields set), with the
	// time zone dateutil attaches. pandas' parse_datetime_string_with_reso
	// never asks for today's date, so a text without a date is year 1.
	Result Result
}

// ParseFields is parser._parse and pandas' dateutil_parse for text: dateutil
// with its default arguments (dayfirst=False, yearfirst=False), the fields it
// sets, and the datetime built from pandas' _DEFAULT_DATETIME. Unlike Parse,
// the fields the text omits are the first of January of year 1, not today's
// date: this is the reading pandas makes for a DatetimeIndex key.
//
// The error is an *Error for a Python exception: a text dateutil does not read
// is ParserError (pandas' DateParseError), and a field datetime.replace
// refuses is the ValueError (DateParseError) or the OverflowError
// (OutOfBoundsDatetime, a ValueError) pandas raises. A weekday without a day
// is no exception here: pandas rejects it after the datetime is built, so
// Weekday and Day say so.
func ParseFields(text string, pivotYear int, local *time.Location) (Fields, error) {
	if local == nil {
		return Fields{}, ErrUnexpectedZone
	}
	chars := pystr.RunesReplace(text)
	pythonText := func() string { return pystr.String(chars) }
	p := &parser{pivot: int64(pivotYear)}
	if !p.parse(chars) {
		if p.raised != nil {
			return Fields{}, p.raised
		}
		return Fields{}, &Error{Class: ClassParserError, Message: "Unknown datetime string format, unable to parse: " + pythonText()}
	}
	if p.res.empty() {
		return Fields{}, &Error{Class: ClassParserError, Message: "Unable to parse datetime string: " + pythonText()}
	}
	result, err := buildNaive(&p.res, 1, 1, 1)
	if err != nil {
		if err.Class == classCaughtValueError {
			return Fields{}, &Error{Class: ClassParserError, Message: err.Message + ": " + pythonText()}
		}
		// OutOfBoundsDatetime is a ValueError.
		return Fields{}, &Error{Class: ClassParserError, Message: "Parsing \"" + pythonText() + "\" to datetime overflows"}
	}
	aware, zoneErr := buildAware(result, &p.res, local)
	if zoneErr != nil {
		return Fields{}, zoneErr
	}
	return Fields{Year: p.res.year, Month: p.res.month, Day: p.res.day, Hour: p.res.hour, Minute: p.res.minute,
		Second: p.res.second, Microsecond: p.res.microsecond, HasMicrosecond: p.res.hasMicrosecond, Weekday: p.res.weekday,
		Result: aware}, nil
}
