package dto

import (
	"encoding/json"
	"sort"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// PythonJSONDumpsIndent is json.dumps(value, indent=indent): ASCII escaping,
// insertion order, ',' between items and ': ' after keys, one item per line,
// and '[]' / '{}' for empty containers. It accepts the values
// PythonJSONDumps accepts.
func PythonJSONDumpsIndent(value any, indent int) []byte {
	var out strings.Builder
	pythonJSONWriteIndent(&out, value, indent, 0, false)
	return []byte(out.String())
}

// PythonJSONDumpsSortedIndent is json.dumps(value, sort_keys=True, indent=indent)
// (stripe's StripeObject.__str__).
func PythonJSONDumpsSortedIndent(value any, indent int) []byte {
	var out strings.Builder
	pythonJSONWriteIndent(&out, value, indent, 0, true)
	return []byte(out.String())
}

func pythonJSONWriteIndent(out *strings.Builder, value any, indent, level int, sorted bool) {
	newline := func(depth int) {
		out.WriteByte('\n')
		out.WriteString(strings.Repeat(" ", indent*depth))
	}
	switch v := value.(type) {
	case domain.RequestObject:
		if len(v) == 0 {
			out.WriteString("{}")
			return
		}
		if sorted {
			v = append(domain.RequestObject(nil), v...)
			sort.SliceStable(v, func(i, j int) bool { return v[i].Name < v[j].Name })
		}
		out.WriteByte('{')
		for i, field := range v {
			if i > 0 {
				out.WriteByte(',')
			}
			newline(level + 1)
			pythonJSONString(out, field.Name)
			out.WriteString(": ")
			pythonJSONWriteIndent(out, field.Value, indent, level+1, sorted)
		}
		newline(level)
		out.WriteByte('}')
	case []any:
		if len(v) == 0 {
			out.WriteString("[]")
			return
		}
		out.WriteByte('[')
		for i, element := range v {
			if i > 0 {
				out.WriteByte(',')
			}
			newline(level + 1)
			pythonJSONWriteIndent(out, element, indent, level+1, sorted)
		}
		newline(level)
		out.WriteByte(']')
	case json.Number, float64, string, bool, nil:
		pythonJSONWrite(out, v)
	default:
		panic("unsupported reference JSON value")
	}
}
