package associationexport

import (
	"fmt"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrUnsupportedValue is a stored value outside the reproduced subset
// (infinite dates or timestamps, BC dates); the export hands such an
// association to Django before any effect.
var ErrUnsupportedValue = refusal.New("associationexport: value outside the reproduced subset")

// Serialize is serialize_record's value for one field, from the column's
// PostgreSQL text output (session TimeZone UTC); valid is false for NULL.
func Serialize(field Field, text string, valid bool) (any, error) {
	switch field.Kind {
	case "null":
		return nil, nil
	case "fk":
		// str(fk_id) if fk_id else None
		if !valid || text == "" || (field.Target == "int" && text == "0") {
			return nil, nil
		}
		return text, nil
	}
	if !valid {
		return nil, nil
	}
	switch field.Kind {
	case "uuid", "text":
		return text, nil
	case "int":
		return Int(text), nil
	case "bool":
		switch text {
		case "t":
			return true, nil
		case "f":
			return false, nil
		}
	case "decimal":
		return DecimalString(text)
	case "datetime":
		t, err := parseTimestamp(text)
		if err != nil {
			return nil, err
		}
		return PythonISOFormat(t), nil
	case "date":
		if len(text) != 10 || text[4] != '-' || text[7] != '-' {
			return nil, ErrUnsupportedValue
		}
		return text, nil
	case "time":
		return djangoTime(text)
	case "json":
		return FromJSONB(text)
	}
	return nil, fmt.Errorf("associationexport: %s value of kind %s", field.Key, field.Kind)
}

func parseTimestamp(text string) (time.Time, error) {
	if strings.HasPrefix(text, "infinity") || strings.HasPrefix(text, "-infinity") || strings.HasSuffix(text, " BC") {
		return time.Time{}, ErrUnsupportedValue
	}
	t, err := time.Parse("2006-01-02 15:04:05.999999-07", text)
	if err != nil {
		return time.Time{}, ErrUnsupportedValue
	}
	return t.UTC(), nil
}

// PythonISOFormat is datetime.isoformat() of an aware UTC datetime.
func PythonISOFormat(t time.Time) string {
	t = t.UTC()
	out := fmt.Sprintf("%04d-%02d-%02dT%02d:%02d:%02d", t.Year(), int(t.Month()), t.Day(), t.Hour(), t.Minute(), t.Second())
	if micro := t.Nanosecond() / 1000; micro != 0 {
		out += fmt.Sprintf(".%06d", micro)
	}
	return out + "+00:00"
}

// djangoTime is DjangoJSONEncoder's time: isoformat() cut to milliseconds.
func djangoTime(text string) (string, error) {
	if len(text) < 8 || text[2] != ':' || text[5] != ':' || strings.ContainsAny(text, "+-") {
		return "", ErrUnsupportedValue
	}
	whole, fraction, found := strings.Cut(text, ".")
	if !found || strings.Trim(fraction, "0") == "" {
		return whole, nil
	}
	fraction = (fraction + "000000")[:6]
	return whole + "." + fraction[:3], nil
}

// DecimalString is str(Decimal(text)) for PostgreSQL numeric output.
func DecimalString(text string) (string, error) {
	switch text {
	case "NaN", "Infinity", "-Infinity":
		return text, nil
	}
	sign := ""
	if strings.HasPrefix(text, "-") {
		sign, text = "-", text[1:]
	}
	whole, fraction, _ := strings.Cut(text, ".")
	if whole == "" || strings.Trim(whole+fraction, "0123456789") != "" {
		return "", ErrUnsupportedValue
	}
	coefficient := strings.TrimLeft(whole+fraction, "0")
	if coefficient == "" {
		coefficient = "0"
	}
	exponent := -len(fraction)
	adjusted := exponent + len(coefficient) - 1
	if exponent <= 0 && adjusted >= -6 {
		if exponent == 0 {
			return sign + coefficient, nil
		}
		dot := len(coefficient) + exponent
		if dot <= 0 {
			return sign + "0." + strings.Repeat("0", -dot) + coefficient, nil
		}
		return sign + coefficient[:dot] + "." + coefficient[dot:], nil
	}
	out := coefficient[:1]
	if len(coefficient) > 1 {
		out += "." + coefficient[1:]
	}
	expSign := "+"
	if adjusted < 0 {
		expSign, adjusted = "-", -adjusted
	}
	return fmt.Sprintf("%s%sE%s%d", sign, out, expSign, adjusted), nil
}
