package djtemplate

import (
	"fmt"
	"strings"
	"time"
)

// django.utils.dates WEEKDAYS and MONTHS in the languages the document
// views render dates by name in (the golden data checks them).
var weekdayNames = map[string][7]string{
	"it": {"Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"},
	"en": {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"},
}

var monthNames = map[string][12]string{
	"it": {"Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"},
	"en": {"January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"},
}

// Django's re_formatchars letters.
const dateFormatLetters = "aAbcdDeEfFgGhHiIjlLmMnNoOPrsStTUuwWyYzZ"

// date is the date filter (expects_localtime): "" for None and "", the
// formatted date, or "" when the value has no date attributes. Only the
// d, m, Y, l and F specifiers are ported.
func (r *renderer) date(value any, format string) (any, error) {
	var t time.Time
	switch v := value.(type) {
	case nil:
		return "", nil
	case string:
		if v == "" {
			return "", nil
		}
		return dateOfNonDate(format)
	case Safe:
		if v == "" {
			return "", nil
		}
		return dateOfNonDate(format)
	case bool, int64, float64, Decimal:
		return dateOfNonDate(format)
	case Date:
		t = v.time()
	case DateTime:
		t = v.Time.UTC()
	default:
		return nil, unsupported("date of %T", value)
	}
	var out strings.Builder
	for i := 0; i < len(format); i++ {
		c := format[i]
		if c == '\\' {
			return nil, unsupported("escaped date format")
		}
		if !strings.ContainsRune(dateFormatLetters, rune(c)) {
			out.WriteByte(c)
			continue
		}
		switch c {
		case 'd':
			fmt.Fprintf(&out, "%02d", t.Day())
		case 'm':
			fmt.Fprintf(&out, "%02d", int(t.Month()))
		case 'Y':
			fmt.Fprintf(&out, "%04d", t.Year())
		case 'l':
			names, ok := weekdayNames[r.language]
			if !ok {
				return nil, unsupported("weekday names in %q", r.language)
			}
			out.WriteString(names[(int(t.Weekday())+6)%7])
		case 'F':
			names, ok := monthNames[r.language]
			if !ok {
				return nil, unsupported("month names in %q", r.language)
			}
			out.WriteString(names[int(t.Month())-1])
		default:
			return nil, unsupported("date format %q", string(c))
		}
	}
	return out.String(), nil
}

// dateOfNonDate: DateFormat reads .day/.month/.year first and raises
// AttributeError, which the filter turns into "". A format without
// specifiers would print as is: refused.
func dateOfNonDate(format string) (any, error) {
	for i := 0; i < len(format); i++ {
		if strings.ContainsRune(dateFormatLetters, rune(format[i])) {
			if strings.ContainsRune("dmYlF", rune(format[i])) {
				return "", nil
			}
			return nil, unsupported("date format %q", string(format[i]))
		}
	}
	return nil, unsupported("date format without specifiers")
}
