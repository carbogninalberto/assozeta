package dto

import (
	"encoding/json"
	"errors"
	"math"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// AgentJSON is Python's json.dumps with the options the agent uses:
// ensure_ascii, separators, indent and allow_nan, for parsed reference values
// (domain.RequestObject in insertion order, []any, string, json.Number,
// float64, int, bool, nil).
type AgentJSON struct {
	EnsureASCII bool
	Item, Key   string // separators; ignored with Indent (',' and ': ')
	Indent      int    // > 0: indent=N
	RejectNaN   bool   // allow_nan=False
}

// ErrAgentJSONValue is a value json.dumps would reject (NaN with
// allow_nan=False) or a Go value outside the reference set.
var ErrAgentJSONValue = errors.New("value not serializable as Python JSON")

// Dumps renders value.
func (o AgentJSON) Dumps(value any) (string, error) {
	var out strings.Builder
	if err := o.write(&out, value, 0); err != nil {
		return "", err
	}
	return out.String(), nil
}

func (o AgentJSON) newline(out *strings.Builder, level int) {
	out.WriteByte('\n')
	out.WriteString(strings.Repeat(" ", o.Indent*level))
}

func (o AgentJSON) write(out *strings.Builder, value any, level int) error {
	item, key := o.Item, o.Key
	if o.Indent > 0 {
		item, key = ",", ": "
	}
	switch v := value.(type) {
	case nil:
		out.WriteString("null")
	case bool:
		out.WriteString(map[bool]string{true: "true", false: "false"}[v])
	case string:
		o.str(out, v)
	case int:
		out.WriteString(strconv.Itoa(v))
	case json.Number:
		text := pythonJSONNumber(string(v))
		if o.RejectNaN && (text == "NaN" || text == "Infinity" || text == "-Infinity") {
			return ErrAgentJSONValue
		}
		out.WriteString(text)
	case float64:
		if o.RejectNaN && (math.IsNaN(v) || math.IsInf(v, 0)) {
			return ErrAgentJSONValue
		}
		out.WriteString(pythonFloatRepr(v))
	case domain.RequestObject:
		if len(v) == 0 {
			out.WriteString("{}")
			return nil
		}
		out.WriteByte('{')
		for i, field := range v {
			if i > 0 {
				out.WriteString(item)
			}
			if o.Indent > 0 {
				o.newline(out, level+1)
			}
			o.str(out, field.Name)
			out.WriteString(key)
			if err := o.write(out, field.Value, level+1); err != nil {
				return err
			}
		}
		if o.Indent > 0 {
			o.newline(out, level)
		}
		out.WriteByte('}')
	case []any:
		if len(v) == 0 {
			out.WriteString("[]")
			return nil
		}
		out.WriteByte('[')
		for i, element := range v {
			if i > 0 {
				out.WriteString(item)
			}
			if o.Indent > 0 {
				o.newline(out, level+1)
			}
			if err := o.write(out, element, level+1); err != nil {
				return err
			}
		}
		if o.Indent > 0 {
			o.newline(out, level)
		}
		out.WriteByte(']')
	default:
		return ErrAgentJSONValue
	}
	return nil
}

// str writes a JSON string: py_encode_basestring_ascii or, without
// ensure_ascii, py_encode_basestring (only quotes, backslashes and control
// characters escaped; preserved surrogate units stay as written).
func (o AgentJSON) str(out *strings.Builder, value string) {
	if o.EnsureASCII {
		pythonJSONString(out, value)
		return
	}
	const hex = "0123456789abcdef"
	out.WriteByte('"')
	for i := 0; i < len(value); i++ {
		c := value[i]
		switch {
		case c == '"':
			out.WriteString(`\"`)
		case c == '\\':
			out.WriteString(`\\`)
		case c == '\n':
			out.WriteString(`\n`)
		case c == '\r':
			out.WriteString(`\r`)
		case c == '\t':
			out.WriteString(`\t`)
		case c == '\b':
			out.WriteString(`\b`)
		case c == '\f':
			out.WriteString(`\f`)
		case c < 0x20:
			out.WriteString(`\u00`)
			out.WriteByte(hex[c>>4])
			out.WriteByte(hex[c&0xf])
		default:
			out.WriteByte(c)
		}
	}
	out.WriteByte('"')
}

// AgentHasSurrogate reports a preserved lone surrogate (WTF-8) in text: such
// a Python str cannot be encoded as UTF-8.
func AgentHasSurrogate(text string) bool {
	for i := 0; i+2 < len(text); i++ {
		if text[i] == 0xed && text[i+1] >= 0xa0 && text[i+1] <= 0xbf {
			return true
		}
	}
	return false
}

// AgentCodePoints is len(str) for a parsed reference string.
func AgentCodePoints(text string) []rune { return pythonCodePoints(text) }

// AgentFromCodePoints rebuilds a reference string from code points
// (surrogate units as WTF-8).
func AgentFromCodePoints(points []rune) string {
	var out strings.Builder
	for _, r := range points {
		if r >= 0xd800 && r <= 0xdfff {
			out.WriteByte(0xed)
			out.WriteByte(byte(0xa0 | (r>>6)&0x1f))
			out.WriteByte(byte(0x80 | r&0x3f))
			continue
		}
		out.WriteRune(r)
	}
	return out.String()
}
