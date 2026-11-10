package celery

import (
	"encoding/hex"
	"fmt"
	"math"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// Dict is a Python dict with string keys, in insertion order.
type Dict []Item

// Item is one Dict entry.
type Item struct {
	Key   string
	Value any
}

// tuple is a Python tuple (the task's positional arguments).
type tuple []any

// ErrUnsupportedValue is a task argument the producer cannot serialize the
// way kombu's JSON serializer and Celery's saferepr would.
var ErrUnsupportedValue = refusal.New("celery: unsupported task argument")

// Supported argument values: nil (None), bool, int, int64, string (valid
// UTF-8), uuid.UUID, []any (list) and Dict. Floats, Decimals, dates and
// bytes are refused: their Python encodings are not reproduced here.

// appendJSON is kombu.utils.json.dumps: json.dumps with ensure_ascii and
// the default ", " / ": " separators; UUIDs use kombu's registered type.
func appendJSON(b []byte, value any) ([]byte, error) {
	switch v := value.(type) {
	case nil:
		return append(b, "null"...), nil
	case bool:
		if v {
			return append(b, "true"...), nil
		}
		return append(b, "false"...), nil
	case int:
		return strconv.AppendInt(b, int64(v), 10), nil
	case int64:
		return strconv.AppendInt(b, v, 10), nil
	case Number:
		return appendNumber(b, v)
	case string:
		return appendJSONString(b, v)
	case uuid.UUID:
		b = append(b, `{"__type__": "uuid", "__value__": {"hex": "`...)
		b = append(b, hex.EncodeToString(v[:])...)
		return append(b, `"}}`...), nil
	case []any:
		return appendJSONList(b, v)
	case tuple:
		return appendJSONList(b, v)
	case Dict:
		b = append(b, '{')
		var err error
		for i, item := range v {
			if i > 0 {
				b = append(b, ", "...)
			}
			if b, err = appendJSONString(b, item.Key); err != nil {
				return nil, err
			}
			b = append(b, ": "...)
			if b, err = appendJSON(b, item.Value); err != nil {
				return nil, err
			}
		}
		return append(b, '}'), nil
	}
	return nil, fmt.Errorf("%w: %T", ErrUnsupportedValue, value)
}

func appendJSONList(b []byte, items []any) ([]byte, error) {
	b = append(b, '[')
	var err error
	for i, item := range items {
		if i > 0 {
			b = append(b, ", "...)
		}
		if b, err = appendJSON(b, item); err != nil {
			return nil, err
		}
	}
	return append(b, ']'), nil
}

// appendJSONString is json.dumps's ensure_ascii string: everything outside
// printable ASCII is a lowercase \uXXXX escape (surrogate pairs above U+FFFF).
func appendJSONString(b []byte, s string) ([]byte, error) {
	if !utf8.ValidString(s) {
		return nil, fmt.Errorf("%w: invalid UTF-8 text", ErrUnsupportedValue)
	}
	b = append(b, '"')
	for _, r := range s {
		switch r {
		case '"':
			b = append(b, `\"`...)
		case '\\':
			b = append(b, `\\`...)
		case '\n':
			b = append(b, `\n`...)
		case '\r':
			b = append(b, `\r`...)
		case '\t':
			b = append(b, `\t`...)
		case '\b':
			b = append(b, `\b`...)
		case '\f':
			b = append(b, `\f`...)
		default:
			switch {
			case r >= 0x20 && r <= 0x7e:
				b = append(b, byte(r))
			case r > 0xffff:
				r -= 0x10000
				b = fmt.Appendf(b, `\u%04x\u%04x`, 0xd800|(r>>10), 0xdc00|(r&0x3ff))
			default:
				b = fmt.Appendf(b, `\u%04x`, r)
			}
		}
	}
	return append(b, '"'), nil
}

// pythonFloat is repr(float) for the publish stamp (time.time()).
func pythonFloat(value float64) string {
	if math.IsInf(value, 0) || math.IsNaN(value) || math.Abs(value) >= 1e16 || (value != 0 && math.Abs(value) < 1e-4) {
		// Not a wall-clock stamp; repr would switch to exponent notation.
		return strconv.FormatFloat(value, 'g', -1, 64)
	}
	text := strconv.FormatFloat(value, 'f', -1, 64)
	if !strings.Contains(text, ".") {
		text += ".0"
	}
	return text
}

// pythonISOFormat is datetime.isoformat() of an aware UTC datetime (the
// countdown/expires conversion Celery performs with app.now()).
func pythonISOFormat(value time.Time) string {
	value = value.UTC().Truncate(time.Microsecond)
	text := value.Format("2006-01-02T15:04:05")
	if micro := value.Nanosecond() / 1000; micro != 0 {
		text += fmt.Sprintf(".%06d", micro)
	}
	return text + "+00:00"
}

// --- celery.utils.saferepr ---------------------------------------------------

const reprMaxLevels = 3

type reprKind int

const (
	reprLiteral reprKind = iota
	reprKey
	reprQuoted
	reprPlain
)

type reprToken struct {
	kind     reprKind
	text     string
	truncate bool // literal separators are dropped after the size limit
	opener   int  // for a closing literal: the index of its opening literal
}

type reprStream struct {
	tokens []reprToken
	level  int
	open   []int
}

func (s *reprStream) literal(text string, truncate bool, direction int) {
	token := reprToken{kind: reprLiteral, text: text, truncate: truncate, opener: -1}
	switch direction {
	case 1:
		s.open = append(s.open, len(s.tokens))
	case -1:
		token.opener = s.open[len(s.open)-1]
		s.open = s.open[:len(s.open)-1]
	}
	s.level += direction
	s.tokens = append(s.tokens, token)
}

// value follows reprstream: scalars become tokens; containers past
// maxlevels are rendered as "{...}"-style placeholders.
func (s *reprStream) value(value any) error {
	switch v := value.(type) {
	case nil:
		s.tokens = append(s.tokens, reprToken{kind: reprPlain, text: "None"})
	case bool:
		text := "False"
		if v {
			text = "True"
		}
		s.tokens = append(s.tokens, reprToken{kind: reprPlain, text: text})
	case int:
		s.tokens = append(s.tokens, reprToken{kind: reprPlain, text: strconv.Itoa(v)})
	case int64:
		s.tokens = append(s.tokens, reprToken{kind: reprPlain, text: strconv.FormatInt(v, 10)})
	case Number:
		// Integers only: a decoded float's repr is not reproduced here.
		if strings.ContainsAny(string(v), ".eE") {
			return fmt.Errorf("%w: float", ErrUnsupportedValue)
		}
		s.tokens = append(s.tokens, reprToken{kind: reprPlain, text: string(v)})
	case string:
		if !utf8.ValidString(v) {
			return fmt.Errorf("%w: invalid UTF-8 text", ErrUnsupportedValue)
		}
		s.tokens = append(s.tokens, reprToken{kind: reprQuoted, text: v})
	case uuid.UUID:
		s.tokens = append(s.tokens, reprToken{kind: reprPlain, text: "UUID('" + v.String() + "')"})
	case tuple:
		end := ")"
		if len(v) == 1 {
			end = ",)"
		}
		return s.sequence("(", end, v)
	case []any:
		return s.sequence("[", "]", v)
	case Dict:
		if s.level >= reprMaxLevels {
			s.tokens = append(s.tokens, reprToken{kind: reprPlain, text: "{...}"})
			return nil
		}
		s.literal("{", false, 1)
		for i, item := range v {
			if !utf8.ValidString(item.Key) {
				return fmt.Errorf("%w: invalid UTF-8 key", ErrUnsupportedValue)
			}
			s.tokens = append(s.tokens, reprToken{kind: reprKey, text: item.Key})
			s.literal(": ", true, 0)
			if err := s.value(item.Value); err != nil {
				return err
			}
			if i < len(v)-1 {
				s.literal(", ", true, 0)
			}
		}
		s.literal("}", false, -1)
	default:
		return fmt.Errorf("%w: %T", ErrUnsupportedValue, value)
	}
	return nil
}

func (s *reprStream) sequence(start, end string, items []any) error {
	if s.level >= reprMaxLevels {
		s.tokens = append(s.tokens, reprToken{kind: reprPlain, text: start + "..." + end})
		return nil
	}
	s.literal(start, false, 1)
	for i, item := range items {
		if err := s.value(item); err != nil {
			return err
		}
		if i < len(items)-1 {
			s.literal(", ", true, 0)
		}
	}
	s.literal(end, false, -1)
	return nil
}

// saferepr is celery.utils.saferepr.saferepr(value, maxlen) (maxlevels 3):
// strings are quoted with only ' escaped, text is truncated with "...", and
// once maxlen is spent ", ..." is followed by the open containers' closers.
func saferepr(value any, maxlen int) (string, error) {
	stream := &reprStream{}
	if err := stream.value(value); err != nil {
		return "", err
	}
	var out strings.Builder
	for i, token := range stream.tokens {
		if maxlen <= 0 {
			out.WriteString(", ...")
			for _, rest := range stream.tokens[i+1:] {
				if rest.kind == reprLiteral && !rest.truncate && rest.opener >= 0 && rest.opener <= i {
					out.WriteString(rest.text)
				}
			}
			break
		}
		var text string
		switch token.kind {
		case reprLiteral:
			text = token.text
		case reprKey, reprQuoted:
			text = "'" + strings.ReplaceAll(truncateText(token.text, maxlen), "'", `\'`) + "'"
		default:
			text = truncateText(token.text, maxlen)
		}
		out.WriteString(text)
		maxlen -= utf8.RuneCountInString(text)
	}
	return out.String(), nil
}

// truncateText is celery.utils.text.truncate(s, maxlen).
func truncateText(s string, maxlen int) string {
	runes := []rune(s)
	if maxlen == 0 || len(runes) < maxlen {
		return s
	}
	head := runes[:maxlen]
	for i := len(head) - 1; i >= 0; i-- {
		if head[i] == ' ' {
			head = head[:i]
			break
		}
	}
	return string(head) + "..."
}
