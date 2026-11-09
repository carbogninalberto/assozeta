package apischema

import (
	"fmt"
	"regexp"
	"strings"
	"unicode/utf8"
)

// Value is the subset of DRF response data the schema views render for
// errors: Map (an ordered dict), []Value, ErrorDetail and string.
type Value any

// Pair is one entry of an ordered Map.
type Pair struct {
	Key   string
	Value Value
}

// Map is a Python dict in insertion order.
type Map []Pair

// ErrorDetail is rest_framework.exceptions.ErrorDetail: a string for JSON,
// {'string': ..., 'code': ...} for drf-spectacular's YAML renderer.
type ErrorDetail struct {
	String, Code string
}

// RenderJSON is DRF's JSONRenderer with an indent (OpenApiJsonRenderer never
// renders without one): json.dumps(..., ensure_ascii=False, separators=(',',
// ': ')) with U+2028/U+2029 escaped afterwards.
func RenderJSON(value Value, indent int) string {
	var out strings.Builder
	renderJSON(&out, value, indent, 0)
	return out.String()
}

func renderJSON(out *strings.Builder, value Value, indent, depth int) {
	newline := func(level int) {
		out.WriteByte('\n')
		out.WriteString(strings.Repeat(" ", indent*level))
	}
	switch v := value.(type) {
	case Map:
		if len(v) == 0 {
			out.WriteString("{}")
			return
		}
		out.WriteByte('{')
		for i, pair := range v {
			if i > 0 {
				out.WriteByte(',')
			}
			newline(depth + 1)
			writeJSONString(out, pair.Key)
			out.WriteString(": ")
			renderJSON(out, pair.Value, indent, depth+1)
		}
		newline(depth)
		out.WriteByte('}')
	case []Value:
		if len(v) == 0 {
			out.WriteString("[]")
			return
		}
		out.WriteByte('[')
		for i, item := range v {
			if i > 0 {
				out.WriteByte(',')
			}
			newline(depth + 1)
			renderJSON(out, item, indent, depth+1)
		}
		newline(depth)
		out.WriteByte(']')
	case ErrorDetail:
		writeJSONString(out, v.String)
	case string:
		writeJSONString(out, v)
	default:
		panic(fmt.Sprintf("apischema: unsupported JSON value %T", value))
	}
}

func writeJSONString(out *strings.Builder, value string) {
	out.WriteByte('"')
	for _, r := range value {
		switch r {
		case '"':
			out.WriteString(`\"`)
		case '\\':
			out.WriteString(`\\`)
		case '\n':
			out.WriteString(`\n`)
		case '\r':
			out.WriteString(`\r`)
		case '\t':
			out.WriteString(`\t`)
		case '\b':
			out.WriteString(`\b`)
		case '\f':
			out.WriteString(`\f`)
		case '\u2028', '\u2029':
			fmt.Fprintf(out, `\u%04x`, r)
		default:
			if r < 0x20 {
				fmt.Fprintf(out, `\u%04x`, r)
			} else {
				out.WriteRune(r)
			}
		}
	}
	out.WriteByte('"')
}

// RenderYAML is drf-spectacular's OpenApiYamlRenderer (PyYAML SafeDumper,
// block style, allow_unicode, width 80, indent 2) for Value data.
func RenderYAML(value Value) string {
	e := &yamlEmitter{whitespace: true, indention: true}
	e.node(value, 0, false, true)
	e.writeIndent(0)
	if e.openEnded {
		e.writeIndicator("...", true, false, false)
		e.writeIndent(0)
	}
	out := e.out.String()
	// expect_document_end writes the final line break through write_indent.
	return out
}

type yamlEmitter struct {
	out        strings.Builder
	column     int
	whitespace bool
	indention  bool
	openEnded  bool
}

func (e *yamlEmitter) write(data string) {
	e.out.WriteString(data)
	e.column += utf8.RuneCountInString(data)
}

func (e *yamlEmitter) writeIndicator(indicator string, needWhitespace, whitespace, indention bool) {
	data := indicator
	if !e.whitespace && needWhitespace {
		data = " " + indicator
	}
	e.whitespace = whitespace
	e.indention = e.indention && indention
	e.openEnded = false
	e.write(data)
}

func (e *yamlEmitter) writeLineBreak(data string) {
	if data == "" {
		data = "\n"
	}
	e.whitespace, e.indention = true, true
	e.out.WriteString(data)
	e.column = 0
}

func (e *yamlEmitter) writeIndent(indent int) {
	if !e.indention || e.column > indent || (e.column == indent && !e.whitespace) {
		e.writeLineBreak("")
	}
	if e.column < indent {
		e.whitespace = true
		e.out.WriteString(strings.Repeat(" ", indent-e.column))
		e.column = indent
	}
}

// node emits value as the content of a block collection. indent is the
// enclosing block indent; inSequence/root mirror PyYAML's contexts.
func (e *yamlEmitter) node(value Value, indent int, mappingValue, root bool) {
	switch v := value.(type) {
	case ErrorDetail:
		e.node(Map{{"string", v.String}, {"code", v.Code}}, indent, mappingValue, root)
	case Map:
		childIndent := indent + 2
		if root {
			childIndent = 0
		}
		if len(v) == 0 {
			e.writeIndicator("{}", true, false, false)
			return
		}
		for _, pair := range v {
			e.writeIndent(childIndent)
			e.scalar(pair.Key, childIndent, true)
			e.writeIndicator(":", false, false, false)
			e.child(pair.Value, childIndent)
		}
	case []Value:
		if len(v) == 0 {
			e.writeIndicator("[]", true, false, false)
			return
		}
		// A block sequence that is a mapping value is indentless.
		sequenceIndent := indent + 2
		if mappingValue && !e.indention {
			sequenceIndent = indent
		}
		for _, item := range v {
			e.writeIndent(sequenceIndent)
			e.writeIndicator("-", true, false, true)
			e.child(item, sequenceIndent)
		}
	case string:
		e.scalar(v, indent+2, false)
	default:
		panic(fmt.Sprintf("apischema: unsupported YAML value %T", value))
	}
}

func (e *yamlEmitter) child(value Value, indent int) {
	switch value.(type) {
	case string:
		e.scalar(value.(string), indent+2, false)
	default:
		e.node(value, indent, true, false)
	}
}

var yamlBreaks = "\n\u0085\u2028\u2029"

func isYAMLBreak(r rune) bool { return strings.ContainsRune(yamlBreaks, r) }

func isYAMLSpaceOrBreak(r rune) bool {
	return r == 0 || r == ' ' || r == '\t' || r == '\r' || isYAMLBreak(r)
}

type scalarAnalysis struct {
	empty, multiline                                         bool
	allowFlowPlain, allowBlockPlain, allowSingle, allowBlock bool
}

func analyzeScalar(scalar []rune) scalarAnalysis {
	if len(scalar) == 0 {
		return scalarAnalysis{empty: true, allowBlockPlain: true, allowSingle: true}
	}
	var blockIndicators, flowIndicators, lineBreaks, special bool
	var leadingSpace, leadingBreak, trailingSpace, trailingBreak, breakSpace, spaceBreak bool
	text := string(scalar)
	if strings.HasPrefix(text, "---") || strings.HasPrefix(text, "...") {
		blockIndicators, flowIndicators = true, true
	}
	precededByWhitespace := true
	followedByWhitespace := len(scalar) == 1 || isYAMLSpaceOrBreak(scalar[1])
	previousSpace, previousBreak := false, false
	for index, ch := range scalar {
		if index == 0 {
			if strings.ContainsRune("#,[]{}&*!|>'\"%@`", ch) {
				flowIndicators, blockIndicators = true, true
			}
			if ch == '?' || ch == ':' {
				flowIndicators = true
				if followedByWhitespace {
					blockIndicators = true
				}
			}
			if ch == '-' && followedByWhitespace {
				flowIndicators, blockIndicators = true, true
			}
		} else {
			if strings.ContainsRune(",?[]{}", ch) {
				flowIndicators = true
			}
			if ch == ':' {
				flowIndicators = true
				if followedByWhitespace {
					blockIndicators = true
				}
			}
			if ch == '#' && precededByWhitespace {
				flowIndicators, blockIndicators = true, true
			}
		}
		if isYAMLBreak(ch) {
			lineBreaks = true
		}
		if !(ch == '\n' || (ch >= 0x20 && ch <= 0x7E)) {
			printable := ch == 0x85 || (ch >= 0xA0 && ch <= 0xD7FF) || (ch >= 0xE000 && ch <= 0xFFFD) ||
				(ch >= 0x10000 && ch < 0x10FFFF)
			if !printable || ch == 0xFEFF {
				special = true
			}
		}
		switch {
		case ch == ' ':
			if index == 0 {
				leadingSpace = true
			}
			if index == len(scalar)-1 {
				trailingSpace = true
			}
			if previousBreak {
				breakSpace = true
			}
			previousSpace, previousBreak = true, false
		case isYAMLBreak(ch):
			if index == 0 {
				leadingBreak = true
			}
			if index == len(scalar)-1 {
				trailingBreak = true
			}
			if previousSpace {
				spaceBreak = true
			}
			previousSpace, previousBreak = false, true
		default:
			previousSpace, previousBreak = false, false
		}
		precededByWhitespace = isYAMLSpaceOrBreak(ch)
		followedByWhitespace = index+2 >= len(scalar) || isYAMLSpaceOrBreak(scalar[index+2])
	}
	a := scalarAnalysis{multiline: lineBreaks, allowFlowPlain: true, allowBlockPlain: true, allowSingle: true, allowBlock: true}
	if leadingSpace || leadingBreak || trailingSpace || trailingBreak {
		a.allowFlowPlain, a.allowBlockPlain = false, false
	}
	if trailingSpace {
		a.allowBlock = false
	}
	if breakSpace {
		a.allowFlowPlain, a.allowBlockPlain, a.allowSingle = false, false, false
	}
	if spaceBreak || special {
		a.allowFlowPlain, a.allowBlockPlain, a.allowSingle, a.allowBlock = false, false, false, false
	}
	if lineBreaks {
		a.allowFlowPlain, a.allowBlockPlain = false, false
	}
	if flowIndicators {
		a.allowFlowPlain = false
	}
	if blockIndicators {
		a.allowBlockPlain = false
	}
	return a
}

// PyYAML's implicit resolvers, keyed by first character as Resolver does.
var (
	yamlBool      = regexp.MustCompile(`^(?:yes|Yes|YES|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF)$`)
	yamlFloat     = regexp.MustCompile(`^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?|\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$`)
	yamlInt       = regexp.MustCompile(`^(?:[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-fA-F_]+|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+)$`)
	yamlMerge     = regexp.MustCompile(`^(?:<<)$`)
	yamlNull      = regexp.MustCompile(`^(?:~|null|Null|NULL|)$`)
	yamlTimestamp = regexp.MustCompile(`^(?:[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]|[0-9][0-9][0-9][0-9]-[0-9][0-9]?-[0-9][0-9]?(?:[Tt]|[ \t]+)[0-9][0-9]?:[0-9][0-9]:[0-9][0-9](?:\.[0-9]*)?(?:[ \t]*(?:Z|[-+][0-9][0-9]?(?::[0-9][0-9])?))?)$`)
	yamlValue     = regexp.MustCompile(`^(?:=)$`)
)

// resolvesToString reports whether the plain scalar resolves to a str tag.
func resolvesToString(value string) bool {
	first := ""
	if value != "" {
		r, _ := utf8.DecodeRuneInString(value)
		first = string(r)
	}
	type resolver struct {
		first string
		re    *regexp.Regexp
	}
	for _, candidate := range []resolver{
		{"yYnNtTfFoO", yamlBool}, {"-+0123456789.", yamlFloat}, {"-+0123456789", yamlInt}, {"<", yamlMerge},
		{"~nN", yamlNull}, {"0123456789", yamlTimestamp}, {"=", yamlValue},
	} {
		applies := strings.Contains(candidate.first, first) && first != ""
		if candidate.re == yamlNull && value == "" {
			applies = true
		}
		if applies && candidate.re.MatchString(value) {
			return false
		}
	}
	return true
}

// scalar emits a str scalar. indent is the scalar's own indent (the enclosing
// block indent plus two), used for folded continuation lines.
func (e *yamlEmitter) scalar(value string, indent int, simpleKey bool) {
	runes := []rune(value)
	analysis := analyzeScalar(runes)
	literal := strings.Contains(value, "\n") // drf-spectacular's multiline_str_representer
	style := byte('"')
	switch {
	case !literal && resolvesToString(value) && !(simpleKey && (analysis.empty || analysis.multiline)) && analysis.allowBlockPlain:
		style = 0
	case literal && !simpleKey && analysis.allowBlock:
		style = '|'
	case !literal && analysis.allowSingle && !(simpleKey && analysis.multiline):
		// A requested literal that is not allowed falls through to double quotes.
		style = '\''
	}
	split := !simpleKey
	switch style {
	case 0:
		e.writePlain(runes, indent, split)
	case '|':
		e.writeLiteral(runes, indent)
	case '\'':
		e.writeSingleQuoted(runes, indent, split)
	default:
		e.writeDoubleQuoted(runes, indent, split)
	}
}

const yamlWidth = 80

func (e *yamlEmitter) writeBreaks(text []rune) {
	if text[0] == '\n' {
		e.writeLineBreak("")
	}
	for _, br := range text {
		if br == '\n' {
			e.writeLineBreak("")
		} else {
			e.writeLineBreak(string(br))
		}
	}
}

func (e *yamlEmitter) writePlain(text []rune, indent int, split bool) {
	if len(text) == 0 {
		return
	}
	if !e.whitespace {
		e.write(" ")
	}
	e.whitespace, e.indention = false, false
	spaces, breaks := false, false
	start := 0
	for end := 0; end <= len(text); end++ {
		var ch rune = -1
		if end < len(text) {
			ch = text[end]
		}
		switch {
		case spaces:
			if ch != ' ' {
				if start+1 == end && e.column > yamlWidth && split {
					e.writeIndent(indent)
					e.whitespace, e.indention = false, false
				} else {
					e.write(string(text[start:end]))
				}
				start = end
			}
		case breaks:
			if ch == -1 || !isYAMLBreak(ch) {
				e.writeBreaks(text[start:end])
				e.writeIndent(indent)
				e.whitespace, e.indention = false, false
				start = end
			}
		default:
			if ch == -1 || ch == ' ' || isYAMLBreak(ch) {
				e.write(string(text[start:end]))
				start = end
			}
		}
		if ch != -1 {
			spaces, breaks = ch == ' ', isYAMLBreak(ch)
		}
	}
}

func (e *yamlEmitter) writeSingleQuoted(text []rune, indent int, split bool) {
	e.writeIndicator("'", true, false, false)
	spaces, breaks := false, false
	start := 0
	for end := 0; end <= len(text); end++ {
		var ch rune = -1
		if end < len(text) {
			ch = text[end]
		}
		switch {
		case spaces:
			if ch == -1 || ch != ' ' {
				if start+1 == end && e.column > yamlWidth && split && start != 0 && end != len(text) {
					e.writeIndent(indent)
				} else {
					e.write(string(text[start:end]))
				}
				start = end
			}
		case breaks:
			if ch == -1 || !isYAMLBreak(ch) {
				e.writeBreaks(text[start:end])
				e.writeIndent(indent)
				start = end
			}
		default:
			if ch == -1 || ch == ' ' || isYAMLBreak(ch) || ch == '\'' {
				if start < end {
					e.write(string(text[start:end]))
					start = end
				}
			}
		}
		if ch == '\'' {
			e.write("''")
			start = end + 1
		}
		if ch != -1 {
			spaces, breaks = ch == ' ', isYAMLBreak(ch)
		}
	}
	e.writeIndicator("'", false, false, false)
}

var yamlEscapes = map[rune]string{0: "0", 0x07: "a", 0x08: "b", 0x09: "t", 0x0A: "n", 0x0B: "v", 0x0C: "f",
	0x0D: "r", 0x1B: "e", '"': "\"", '\\': "\\", 0x85: "N", 0xA0: "_", 0x2028: "L", 0x2029: "P"}

func (e *yamlEmitter) writeDoubleQuoted(text []rune, indent int, split bool) {
	e.writeIndicator("\"", true, false, false)
	start := 0
	for end := 0; end <= len(text); end++ {
		var ch rune = -1
		if end < len(text) {
			ch = text[end]
		}
		if ch == -1 || strings.ContainsRune("\"\\\u0085\u2028\u2029\uFEFF", ch) ||
			!((ch >= 0x20 && ch <= 0x7E) || (ch >= 0xA0 && ch <= 0xD7FF) || (ch >= 0xE000 && ch <= 0xFFFD)) {
			if start < end {
				e.write(string(text[start:end]))
				start = end
			}
			if ch != -1 {
				var data string
				switch escape, ok := yamlEscapes[ch]; {
				case ok:
					data = "\\" + escape
				case ch <= 0xFF:
					data = fmt.Sprintf("\\x%02X", ch)
				case ch <= 0xFFFF:
					data = fmt.Sprintf("\\u%04X", ch)
				default:
					data = fmt.Sprintf("\\U%08X", ch)
				}
				e.write(data)
				start = end + 1
			}
		}
		if 0 < end && end < len(text)-1 && (ch == ' ' || start >= end) && e.column+(end-start) > yamlWidth && split {
			data := string(text[start:end]) + "\\"
			if start < end {
				start = end
			}
			e.write(data)
			e.writeIndent(indent)
			e.whitespace, e.indention = false, false
			if text[start] == ' ' {
				e.write("\\")
			}
		}
	}
	e.writeIndicator("\"", false, false, false)
}

func (e *yamlEmitter) writeLiteral(text []rune, indent int) {
	hints := ""
	if isYAMLBreak(text[0]) || text[0] == ' ' {
		hints += "2"
	}
	last := text[len(text)-1]
	switch {
	case !isYAMLBreak(last):
		hints += "-"
	case len(text) == 1 || isYAMLBreak(text[len(text)-2]):
		hints += "+"
	}
	e.writeIndicator("|"+hints, true, false, false)
	if strings.HasSuffix(hints, "+") {
		e.openEnded = true
	}
	e.writeLineBreak("")
	breaks := true
	start := 0
	for end := 0; end <= len(text); end++ {
		var ch rune = -1
		if end < len(text) {
			ch = text[end]
		}
		if breaks {
			if ch == -1 || !isYAMLBreak(ch) {
				for _, br := range text[start:end] {
					if br == '\n' {
						e.writeLineBreak("")
					} else {
						e.writeLineBreak(string(br))
					}
				}
				if ch != -1 {
					e.writeIndent(indent)
				}
				start = end
			}
		} else if ch == -1 || isYAMLBreak(ch) {
			e.out.WriteString(string(text[start:end]))
			if ch == -1 {
				e.writeLineBreak("")
			}
			start = end
		}
		if ch != -1 {
			breaks = isYAMLBreak(ch)
		}
	}
}
