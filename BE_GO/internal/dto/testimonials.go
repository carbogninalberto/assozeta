package dto

import (
	"encoding/json"
	"errors"
	"math/big"
	"strconv"
	"strings"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

var (
	ErrTestimonialMissing = errors.New("missing params.")
	ErrTestimonialScore   = errors.New("invalid score.")
	// ErrTestimonialServer stands for the view's unhandled Python exceptions
	// (TypeError on membership, indexing or comparison; IntegrityError on a
	// null text), all rendered as Django's generic 500 page.
	ErrTestimonialServer = errors.New("testimonial request raised")
)

// TestimonialInput is the stored text (TextField str() coercion) and score
// (FloatField float() coercion) of a request the view accepts.
type TestimonialInput struct {
	Text  string
	Score float64
}

// ParseTestimonialInput follows testimonials_create on the parsed body:
// Python membership ('text' in data), indexing, then 1 <= score <= 5.
func ParseTestimonialInput(body any) (TestimonialInput, error) {
	contains := func(key string) (bool, error) {
		switch data := body.(type) {
		case domain.RequestObject:
			for _, field := range data {
				if field.Name == key {
					return true, nil
				}
			}
			return false, nil
		case []any:
			for _, item := range data {
				if text, ok := item.(string); ok && text == key {
					return true, nil
				}
			}
			return false, nil
		case string:
			return strings.Contains(data, key), nil
		}
		return false, ErrTestimonialServer
	}
	hasText, err := contains("text")
	if err != nil {
		return TestimonialInput{}, err
	}
	hasScore, err := contains("score")
	if err != nil {
		return TestimonialInput{}, err
	}
	if !hasText || !hasScore {
		return TestimonialInput{}, ErrTestimonialMissing
	}
	data, ok := body.(domain.RequestObject)
	if !ok {
		return TestimonialInput{}, ErrTestimonialServer
	}
	value := func(key string) any {
		var found any
		for _, field := range data {
			if field.Name == key {
				found = field.Value
			}
		}
		return found
	}
	score, err := testimonialScore(value("score"))
	if err != nil {
		return TestimonialInput{}, err
	}
	text := value("text")
	if text == nil {
		return TestimonialInput{}, ErrTestimonialServer
	}
	return TestimonialInput{Text: PythonStr(text), Score: score}, nil
}

func testimonialScore(value any) (float64, error) {
	switch v := value.(type) {
	case bool:
		if !v {
			return 0, ErrTestimonialScore
		}
		return 1, nil
	case json.Number:
		token := string(v)
		if !strings.ContainsAny(token, ".eE") {
			integer, ok := new(big.Int).SetString(token, 10)
			if !ok {
				return 0, ErrTestimonialServer
			}
			if integer.Cmp(big.NewInt(1)) < 0 || integer.Cmp(big.NewInt(5)) > 0 {
				return 0, ErrTestimonialScore
			}
			return float64(integer.Int64()), nil
		}
		number, _ := strconv.ParseFloat(token, 64)
		// Overflow yields +/-Inf like float(); DRF rejects NaN literals earlier.
		if number < 1 || number > 5 {
			return 0, ErrTestimonialScore
		}
		return number, nil
	}
	return 0, ErrTestimonialServer
}

// PythonStr is str() of a parsed JSON value: strings unchanged, containers as
// their Python repr.
func PythonStr(value any) string {
	if text, ok := value.(string); ok {
		return text
	}
	var out strings.Builder
	pythonRepr(&out, value)
	return out.String()
}

func pythonRepr(out *strings.Builder, value any) {
	switch v := value.(type) {
	case nil:
		out.WriteString("None")
	case bool:
		out.WriteString(map[bool]string{true: "True", false: "False"}[v])
	case string:
		pythonStringRepr(out, v)
	case json.Number:
		text := pythonJSONNumber(string(v))
		switch text {
		case "Infinity":
			text = "inf"
		case "-Infinity":
			text = "-inf"
		case "NaN":
			text = "nan"
		}
		out.WriteString(text)
	case domain.RequestObject:
		out.WriteByte('{')
		for i, field := range v {
			if i > 0 {
				out.WriteString(", ")
			}
			pythonStringRepr(out, field.Name)
			out.WriteString(": ")
			pythonRepr(out, field.Value)
		}
		out.WriteByte('}')
	case []any:
		out.WriteByte('[')
		for i, item := range v {
			if i > 0 {
				out.WriteString(", ")
			}
			pythonRepr(out, item)
		}
		out.WriteByte(']')
	default:
		panic("unsupported reference JSON value")
	}
}

// pythonStringRepr is str.__repr__: single quotes unless the text contains a
// single quote and no double quote, with backslash escapes for the quote,
// backslash, \t \n \r and every non-printable code point.
func pythonStringRepr(out *strings.Builder, value string) {
	points := pythonCodePoints(value)
	quote := '\''
	if strings.ContainsRune(value, '\'') && !strings.ContainsRune(value, '"') {
		quote = '"'
	}
	out.WriteRune(quote)
	for _, r := range points {
		switch {
		case r == quote || r == '\\':
			out.WriteByte('\\')
			out.WriteRune(r)
		case r == '\t':
			out.WriteString(`\t`)
		case r == '\n':
			out.WriteString(`\n`)
		case r == '\r':
			out.WriteString(`\r`)
		case r >= ' ' && r < 0x7f:
			out.WriteRune(r)
		case r >= 0xd800 && r <= 0xdfff:
			out.WriteString(`\u` + strconv.FormatInt(int64(r), 16))
		case r > 0x7f && unicode.IsPrint(r):
			out.WriteRune(r)
		case r <= 0xff:
			out.WriteString(`\x` + leftPad(strconv.FormatInt(int64(r), 16), 2))
		case r <= 0xffff:
			out.WriteString(`\u` + leftPad(strconv.FormatInt(int64(r), 16), 4))
		default:
			out.WriteString(`\U` + leftPad(strconv.FormatInt(int64(r), 16), 8))
		}
	}
	out.WriteRune(quote)
}
