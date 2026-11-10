// Package pydateutil reproduces dateutil.parser.parse(text) of
// python-dateutil 2.9.0.post0 on CPython 3.13 with its default arguments
// (dayfirst=False, yearfirst=False, fuzzy=False, default=None, tzinfos=None):
// the _timelex tokenizer, the English parserinfo tables, the year/month/day
// resolution, the two-digit year pivot, the construction of the naive
// datetime from today's date and the attachment of a time zone, with every
// exception dateutil lets out (class name and str()).
//
// Python reads three things from the process that Parse takes as arguments:
// datetime.now() (the default date, see Parse), time.localtime().tm_year at
// import time (the two-digit year pivot, see ParseWithPivot) and the local
// zone (time.tzname and dateutil.tz.tzlocal).
//
// Character classes follow str.isalpha, str.isdigit, str.isspace and the
// decimal digits int() and Decimal() accept, from tables generated out of
// the oracle interpreter (unicode_tables.go).
//
// Golden corpus: testdata/corpus.jsonl.gz, produced from the real library by
// testdata/gen_corpus.py (run testdata/regen.sh with the oracle interpreter).
package pydateutil

import (
	"fmt"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrUnsupported marks the inputs Parse does not answer because the result
// could not be shown to equal Python's:
//   - text that is not valid UTF-8 (a Python str has no such form) or is
//     longer than MaxTextBytes;
//   - a nil local zone;
//   - a zone name in the text (as in '10:00 CET') that is an abbreviation of
//     a local zone with more than one abbreviation or offset: Python then
//     depends on time.tzname and on dateutil.tz.tzlocal, which are derived
//     from the platform's C library. A local zone with a single abbreviation
//     and offset (UTC, Etc/GMT) is answered.
var ErrUnsupported = refusal.New("pydateutil: input outside the supported subset")

// MaxTextBytes bounds the accepted text. Python has no such bound; far above
// it the arithmetic of decimal.Decimal starts to depend on exponent limits.
const MaxTextBytes = 1 << 16

// Python exception class names carried by Error.
const (
	// ClassParserError is dateutil.parser.ParserError, a ValueError.
	ClassParserError = "ParserError"
	// ClassOverflowError is the builtin OverflowError.
	ClassOverflowError = "OverflowError"
	// ClassInvalidOperation is decimal.InvalidOperation, an ArithmeticError.
	ClassInvalidOperation = "InvalidOperation"
	// ClassValueError is a plain ValueError (not a ParserError).
	ClassValueError = "ValueError"
)

// Error is the exception dateutil.parser.parse raises.
type Error struct {
	// Class is the Python exception class name (one of the Class constants).
	Class string
	// Message is str(exception).
	Message string
}

func (e *Error) Error() string { return e.Class + ": " + e.Message }

// IsValueError reports whether `except ValueError` catches the exception.
// Every Error is caught by `except Exception`.
func (e *Error) IsValueError() bool {
	return e.Class == ClassParserError || e.Class == ClassValueError
}

// Result is the datetime.datetime returned by dateutil.parser.parse. The
// fields are always a valid datetime (year 1..9999). strftime of a year below
// 1000 is platform dependent in CPython, so only the numbers are exposed.
type Result struct {
	Year, Month, Day     int
	Hour, Minute, Second int
	Microsecond          int
	// HasTZ is `dt.tzinfo is not None`.
	HasTZ bool
	// OffsetSeconds is the offset the tzinfo holds. dateutil does not bound
	// it: outside ±24h (exclusive) dt.utcoffset(), str(dt) and dt.isoformat()
	// raise ValueError while dt.date() and dt.strftime('%d/%m/%Y') work.
	OffsetSeconds int64
	// TZName is dt.tzname() when HasTZName; Python returns None otherwise
	// (a numeric offset without a name).
	TZName    string
	HasTZName bool
}

// Date returns dt.date() as year, month, day.
func (r Result) Date() (year, month, day int) { return r.Year, r.Month, r.Day }

// String is str(dt). The error is the ValueError Python raises for an offset
// outside ±24h.
func (r Result) String() (string, error) { return r.format(' ') }

// ISOFormat is dt.isoformat(), with the same error as String.
func (r Result) ISOFormat() (string, error) { return r.format('T') }

func (r Result) format(sep byte) (string, error) {
	var b strings.Builder
	fmt.Fprintf(&b, "%04d-%02d-%02d%c%02d:%02d:%02d", r.Year, r.Month, r.Day, sep, r.Hour, r.Minute, r.Second)
	if r.Microsecond != 0 {
		fmt.Fprintf(&b, ".%06d", r.Microsecond)
	}
	if !r.HasTZ {
		return b.String(), nil
	}
	offset := r.OffsetSeconds
	if offset <= -86400 || offset >= 86400 {
		return "", &Error{Class: ClassValueError, Message: "offset must be a timedelta strictly between " +
			"-timedelta(hours=24) and timedelta(hours=24)."}
	}
	sign := byte('+')
	if offset < 0 {
		sign, offset = '-', -offset
	}
	fmt.Fprintf(&b, "%c%02d:%02d", sign, offset/3600, offset%3600/60)
	if seconds := offset % 60; seconds != 0 {
		fmt.Fprintf(&b, ":%02d", seconds)
	}
	return b.String(), nil
}

// Parse is dateutil.parser.parse(text) evaluated at the instant now in a
// process whose local zone is local. Fields missing from text come from
// Python's default, datetime.now().replace(hour=0, minute=0, second=0,
// microsecond=0), which is the date of now in local; the two-digit year
// pivot is that date's year (see ParseWithPivot).
//
// The error is an *Error for a Python exception, or wraps ErrUnsupported.
// Python also warns (UnknownTimezoneWarning) and returns a naive datetime
// for an unknown zone name; Parse returns that naive result.
func Parse(text string, now time.Time, local *time.Location) (Result, error) {
	if local == nil {
		return Result{}, ErrUnsupported
	}
	return ParseWithPivot(text, now, now.In(local).Year(), local)
}

// ParseWithPivot is Parse with an explicit two-digit year pivot. dateutil
// builds its default parser once, at import, and keeps the local year of
// that moment: a two-digit year is placed within [pivot-50, pivot+49]. Parse
// uses the year of now, which differs only in a process that has been running
// since the previous calendar year.
func ParseWithPivot(text string, now time.Time, pivotYear int, local *time.Location) (Result, error) {
	return parseWith(text, now, pivotYear, local, false)
}

// ParseDayFirst is dateutil.parser.parse(text, dayfirst=True) evaluated like
// Parse: of two or three ambiguous numbers the first is the day when the
// second can be a month ('03/02/1990' is 3 February; '1990-03-02' is 3 February
// too, since the rule also applies after a leading year).
func ParseDayFirst(text string, now time.Time, local *time.Location) (Result, error) {
	if local == nil {
		return Result{}, ErrUnsupported
	}
	return ParseDayFirstWithPivot(text, now, now.In(local).Year(), local)
}

// ParseDayFirstWithPivot is ParseDayFirst with ParseWithPivot's explicit pivot.
func ParseDayFirstWithPivot(text string, now time.Time, pivotYear int, local *time.Location) (Result, error) {
	return parseWith(text, now, pivotYear, local, true)
}

func parseWith(text string, now time.Time, pivotYear int, local *time.Location, dayFirst bool) (Result, error) {
	if local == nil || len(text) > MaxTextBytes || !utf8.ValidString(text) {
		return Result{}, ErrUnsupported
	}
	today := now.In(local)
	p := &parser{pivot: int64(pivotYear), dayFirst: dayFirst}
	if !p.parse(text) {
		if p.raised != nil {
			return Result{}, p.raised
		}
		return Result{}, &Error{Class: ClassParserError, Message: "Unknown string format: " + text}
	}
	if p.res.empty() {
		return Result{}, &Error{Class: ClassParserError, Message: "String does not contain a date: " + text}
	}
	result, err := buildNaive(&p.res, today.Year(), int(today.Month()), today.Day())
	if err != nil {
		if err.Class == classCaughtValueError {
			return Result{}, &Error{Class: ClassParserError, Message: err.Message + ": " + text}
		}
		return Result{}, err
	}
	return buildAware(result, &p.res, local)
}
