package dto

import (
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"
)

// ErrDjangoFieldPrep is a model field rejecting a raw value at save (500).
var ErrDjangoFieldPrep = errors.New("model field rejected the assigned value")

// DjangoBooleanPrep is BooleanField.to_python on a raw assigned value.
func DjangoBooleanPrep(value any) (bool, error) {
	switch v := value.(type) {
	case bool:
		return v, nil
	case json.Number:
		// value in (True, False): 1, 0, 1.0 and 0.0 compare equal.
		if number, err := strconv.ParseFloat(string(v), 64); err == nil && (number == 0 || number == 1) {
			return number == 1, nil
		}
	case string:
		switch v {
		case "t", "True", "1":
			return true, nil
		case "f", "False", "0":
			return false, nil
		}
	}
	return false, ErrDjangoFieldPrep
}

// PythonIntPrep is IntegerField.get_prep_value's int(value).
func PythonIntPrep(value any) (int64, error) {
	switch v := value.(type) {
	case bool:
		if v {
			return 1, nil
		}
		return 0, nil
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			number, err := strconv.ParseFloat(string(v), 64)
			if (err != nil && !errors.Is(err, strconv.ErrRange)) || math.IsInf(number, 0) || math.IsNaN(number) {
				return 0, ErrDjangoFieldPrep
			}
			return pythonBoundedInt(new(big.Float).SetFloat64(math.Trunc(number)))
		}
		integer, ok := new(big.Int).SetString(string(v), 10)
		if !ok || !integer.IsInt64() {
			return 0, ErrDjangoFieldPrep
		}
		return integer.Int64(), nil
	case string:
		// int(str): surrounding whitespace, a sign, digits with single underscores.
		text := strings.TrimFunc(v, unicode.IsSpace)
		if !pythonIntLiteral.MatchString(text) {
			return 0, ErrDjangoFieldPrep
		}
		integer, ok := new(big.Int).SetString(strings.ReplaceAll(text, "_", ""), 10)
		if !ok || !integer.IsInt64() {
			return 0, ErrDjangoFieldPrep
		}
		return integer.Int64(), nil
	}
	return 0, ErrDjangoFieldPrep
}

func pythonBoundedInt(value *big.Float) (int64, error) {
	integer, _ := value.Int(nil)
	if !integer.IsInt64() {
		return 0, ErrDjangoFieldPrep
	}
	return integer.Int64(), nil
}

var pythonIntLiteral = regexp.MustCompile(`^[+-]?[0-9]+(_[0-9]+)*$`)

var djangoDateOnly = regexp.MustCompile(`^(\d{4})-(\d{1,2})-(\d{1,2})$`)

// DjangoDateTimePrep is DateTimeField.to_python for a string: parse_datetime,
// then parse_date at midnight; naive values use the UTC default timezone.
// ok=false is a ValidationError.
func DjangoDateTimePrep(value string) (time.Time, bool) {
	if parsed, err := parsePaymentCategoryDjangoDateTime(value); err == nil {
		return parsed, true
	}
	if match := djangoDateOnly.FindStringSubmatch(value); match != nil {
		year, _ := strconv.Atoi(match[1])
		month, _ := strconv.Atoi(match[2])
		day, _ := strconv.Atoi(match[3])
		date := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
		if date.Year() == year && int(date.Month()) == month && date.Day() == day {
			return date, true
		}
	}
	return time.Time{}, false
}

// DjangoUUIDFromInt is UUIDField.to_python for integers and booleans
// (uuid.UUID(int=value)); floats and out-of-range values raise.
func DjangoUUIDFromInt(value any) (string, error) {
	id, err := parsePaymentCategoryUUID(value)
	if err != nil {
		return "", ErrDjangoFieldPrep
	}
	return id, nil
}
