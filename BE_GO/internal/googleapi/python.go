package googleapi

import (
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"net/url"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type jsonNumber = json.Number

// ErrNotPorted refuses a value whose Python handling this port does not
// reproduce (the views answer 500 for it here).
var ErrNotPorted = errors.New("google: value not ported")

// PythonError is a Python exception raised by the libraries; Mentions is
// `text in str(exception)` for the export view's 'quotaExceeded' check.
type PythonError struct {
	Message string
	// Data is the exception's extra argument (a token endpoint answer), part of str().
	Data any
}

func (e *PythonError) Error() string { return "google: " + e.Message }
func (e *PythonError) Mentions(text string) bool {
	return strings.Contains(e.Message, text) || containsString(e.Data, text)
}

// containsString is `text in repr(value)` for the string-bearing parts of a
// parsed JSON value (keys and string items appear verbatim in repr).
func containsString(value any, text string) bool {
	switch v := value.(type) {
	case string:
		return strings.Contains(v, text)
	case domain.RequestObject:
		for _, field := range v {
			if strings.Contains(field.Name, text) || containsString(field.Value, text) {
				return true
			}
		}
	case []any:
		for _, item := range v {
			if containsString(item, text) {
				return true
			}
		}
	}
	return false
}

// truthy is Python truthiness of a parsed JSON value.
func truthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case json.Number:
		f, err := v.Float64()
		return err != nil || f != 0
	case domain.RequestObject:
		return len(v) > 0
	case []any:
		return len(v) > 0
	}
	return true
}

func lookup(object domain.RequestObject, name string) (any, bool) {
	// json.loads keeps the last value of a repeated key.
	var found any
	present := false
	for _, field := range object {
		if field.Name == name {
			found, present = field.Value, true
		}
	}
	return found, present
}

func parseJSON(data []byte) (any, error) {
	if !utf8.Valid(data) {
		return nil, &PythonError{Message: "UnicodeDecodeError"}
	}
	return dto.ParsePythonJSON(string(data))
}

// quote is urllib.parse.quote(value, safe=”); quotePlus is quote_plus.
func quote(text string) string { return strings.ReplaceAll(url.QueryEscape(text), "+", "%20") }
func quotePlus(text string) string {
	return url.QueryEscape(text)
}

// urlencode is urllib.parse.urlencode of string pairs.
func urlencode(pairs [][2]string) string {
	parts := make([]string, len(pairs))
	for i, pair := range pairs {
		parts[i] = quotePlus(pair[0]) + "=" + quotePlus(pair[1])
	}
	return strings.Join(parts, "&")
}

// pythonInt is int(text) for a str: surrounding whitespace, a sign, ASCII
// digits with single underscores between them.
func pythonInt(text string) (*big.Int, bool) {
	text = strings.TrimSpace(text)
	if strings.HasPrefix(text, "+") || strings.HasPrefix(text, "-") {
		if len(text) == 1 {
			return nil, false
		}
	}
	body := strings.TrimLeft(text, "+-")
	if len(text)-len(body) > 1 || body == "" || body[0] == '_' || body[len(body)-1] == '_' || strings.Contains(body, "__") {
		return nil, false
	}
	for _, char := range body {
		if char != '_' && (char < '0' || char > '9') {
			return nil, false
		}
	}
	value, ok := new(big.Int).SetString(strings.ReplaceAll(text, "_", ""), 10)
	return value, ok
}

// seconds is timedelta(seconds=value) for an int/float/numeric value (microseconds, half-even).
func seconds(value any) (time.Duration, error) {
	var f float64
	switch v := value.(type) {
	case bool:
		if v {
			f = 1
		}
	case json.Number:
		parsed, err := v.Float64()
		if err != nil {
			return 0, ErrNotPorted
		}
		f = parsed
	case int64:
		f = float64(v)
	default:
		return 0, &PythonError{Message: "TypeError: unsupported type for timedelta seconds component"}
	}
	micro := math.RoundToEven(f * 1e6)
	if math.Abs(micro) > 9e15 {
		return 0, ErrNotPorted
	}
	return time.Duration(micro) * time.Microsecond, nil
}

// isoformat is datetime.isoformat() of a naive UTC value.
func isoformat(at time.Time) string {
	at = at.UTC()
	text := at.Format("2006-01-02T15:04:05")
	if micro := at.Nanosecond() / 1000; micro != 0 {
		text += "." + leftPad(micro, 6)
	}
	return text
}

func leftPad(value, width int) string {
	text := strings.Repeat("0", width) + itoa(value)
	return text[len(text)-width:]
}

func itoa(value int) string {
	return big.NewInt(int64(value)).String()
}
