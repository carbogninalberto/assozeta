package service

import (
	"context"
	"encoding/json"
	"math"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/google/uuid"
)

// BusinessMCPStore is the persistence the ported tool functions use.
type BusinessMCPStore interface {
	SavedReports(ctx context.Context, association, creator string) ([]domain.SavedReport, error)
	InsertSavedReport(ctx context.Context, id, association, creator string, write domain.SavedReportWrite) error
	// ActiveUserExists is User.objects.filter(user_id=id).exists() (not deleted).
	ActiveUserExists(ctx context.Context, id string) (bool, error)
	AssociationExists(ctx context.Context, id string) (bool, error)
	BusinessMCPQueryStore
}

// BusinessMCPTools ports the plain tool functions of
// BE/application/mcp_server/server.py that only read or write rows:
// sanitize_text, list_reports and save_report. The association is the
// server's --association-id; arguments are the tool call's (any user_id is
// an argument, as in Django's stdio dispatch).
type BusinessMCPTools struct {
	store       BusinessMCPStore
	association string
	now         func() time.Time
	// group is set for a saved-report run (report_files.go): Course.objects
	// follows the request's X-Group-Id.
	group *string
}

func NewBusinessMCPTools(store BusinessMCPStore, association string, now func() time.Time) *BusinessMCPTools {
	return &BusinessMCPTools{store: store, association: association, now: now}
}

// Execute dispatches like create_mcp_server's call_tool for the ported
// tools; arguments are the parsed call arguments in their JSON order and
// the returned value is the JSON result Django serialises.
func (t *BusinessMCPTools) Execute(ctx context.Context, name string, arguments domain.RequestObject) (any, error) {
	arguments = dto.PythonDict(arguments)
	switch name {
	case "sanitize_text":
		return t.sanitize(ctx, arguments)
	case "list_reports":
		return t.listReports(ctx, arguments)
	case "save_report":
		return t.saveReport(ctx, arguments)
	case "get_schema":
		return t.schema(ctx, arguments)
	case "query_data":
		return t.query(ctx, name, mcpKwargs(arguments, mcpQueryArguments...))
	case "count_data":
		return t.query(ctx, name, mcpKwargs(arguments, "model_name", "filters", "exclude", "or_filters", "include_archived"))
	case "get_field_values":
		return t.query(ctx, name, mcpKwargs(arguments, "model_name", "field_name", "filters", "exclude", "or_filters", "limit",
			"include_archived"))
	case "aggregate_data":
		return t.query(ctx, name, mcpKwargs(arguments, "model_name", "filters", "exclude", "or_filters", "aggregations", "group_by",
			"include_archived"))
	case "export_data":
		return t.exportData(ctx, arguments)
	case "export_multi_sheet":
		return t.exportMultiSheet(ctx, arguments)
	case "get_attendance_matrix":
		return t.attendance(ctx, arguments)
	}
	return nil, errBusinessMCPUnknownTool
}

// errBusinessMCPUnknownTool is a tool name outside Execute's switch: every
// caller answers unknown names first (businessmcp.Calls's "Unknown tool" and
// the agent's executeTool), so no input reaches it.
var errBusinessMCPUnknownTool = refusal.AppState("business MCP tool name outside the ported tools")

// errBusinessMCPReplacement is a replacement whose re.escape pattern Go
// rejects: the pattern is a quoted string, so no value produces it.
var errBusinessMCPReplacement = refusal.AppState("business MCP sanitize replacement without a pattern")

// BusinessToolNames are the business tools in TOOL_DEFINITIONS order.
var BusinessToolNames = []string{"get_schema", "query_data", "count_data", "get_field_values", "export_data", "aggregate_data",
	"get_attendance_matrix", "export_multi_sheet", "sanitize_text", "save_report", "list_reports"}

// businessRequired are the tool functions' required parameters (besides
// sport_association_id): a call without one is a TypeError.
var businessRequired = map[string][]string{"query_data": {"model_name"}, "count_data": {"model_name"},
	"get_field_values": {"model_name", "field_name"}, "export_data": {"model_name"}, "aggregate_data": {"model_name"},
	"export_multi_sheet": {"sheets"}, "sanitize_text": {"text"}, "save_report": {"name", "tool_name", "params"}}

// BusinessBindingError is the TypeError Python raises binding a call's
// arguments to the tool function (reported as {"error": str(e)}).
func BusinessBindingError(name string, arguments domain.RequestObject) (string, bool) {
	missing := []string{}
	for _, parameter := range businessRequired[name] {
		if _, present := arguments.Lookup(parameter); !present {
			missing = append(missing, "'"+parameter+"'")
		}
	}
	if len(missing) == 0 {
		return "", false
	}
	text := missing[0]
	switch len(missing) {
	case 1:
	case 2:
		text = missing[0] + " and " + missing[1]
	default:
		text = strings.Join(missing[:len(missing)-1], ", ") + ", and " + missing[len(missing)-1]
	}
	noun := "argument"
	if len(missing) > 1 {
		noun = "arguments"
	}
	return "tool_" + name + "() missing " + strconv.Itoa(len(missing)) + " required positional " + noun + ": " + text, true
}

// mcpToolText is an exception raised inside a tool whose str(e) the port
// knows: Python's template-parsing errors and argument type errors.
type mcpToolText struct{ text string }

func (e *mcpToolText) Error() string { return e.text }

// unsafeFilename is _UNSAFE_FILENAME_RE.
var unsafeFilename = regexp.MustCompile(`[<>:"/\\|?*\x00-\x1f]`)

// sanitizeDashRuns is re.escape('-') + '+': the second substitution of
// sanitize_text with the '-' replacement the internal callers pass.
var sanitizeDashRuns = regexp.MustCompile(`-+`)

// sanitizeFilename is sanitize_text(text) (replacement '-'): the internal
// callers (exportData, exportSheets) always pass '-' and cannot fail.
func sanitizeFilename(text string) string {
	if text == "" {
		return text
	}
	sanitized := unsafeFilename.ReplaceAllLiteralString(text, "-")
	sanitized = sanitizeDashRuns.ReplaceAllLiteralString(sanitized, "-")
	sanitized = strings.TrimFunc(sanitized, pythonWhitespace)
	return strings.Trim(sanitized, "-")
}

// sanitizeTemplate is re._parser.parse_template(replacement) for the
// no-group patterns sanitize_text substitutes with: literal text and
// references to the whole match (group 0 is the only valid reference).
type sanitizeTemplate struct{ parts []sanitizePart }

type sanitizePart struct {
	text  string
	match bool
}

func (t sanitizeTemplate) expand(match string) string {
	var out strings.Builder
	for _, part := range t.parts {
		if part.match {
			out.WriteString(match)
		} else {
			out.WriteString(part.text)
		}
	}
	return out.String()
}

// sanitizeTemplateGroups is re._parser.MAXGROUPS.
const sanitizeTemplateGroups = 1073741823

// sanitizeEscapes is re._parser.ESCAPES.
var sanitizeEscapes = map[rune]rune{'a': 7, 'b': 8, 'f': 12, 'n': 10, 'r': 13, 't': 9, 'v': 11, '\\': 92}

const sanitizeDigits = "0123456789"
const sanitizeOctalDigits = "01234567"

// sanitizeTokenizer is re._parser.Tokenizer for a template string: a token is
// one character, or a backslash and the character after it.
type sanitizeTokenizer struct {
	text  []rune
	index int
	next  []rune // nil is Python's None
}

func newSanitizeTokenizer(text string) (*sanitizeTokenizer, error) {
	tokenizer := &sanitizeTokenizer{text: []rune(text)}
	if err := tokenizer.advance(); err != nil {
		return nil, err
	}
	return tokenizer, nil
}

func (t *sanitizeTokenizer) advance() error {
	index := t.index
	if index >= len(t.text) {
		t.next = nil
		return nil
	}
	token := []rune{t.text[index]}
	if token[0] == '\\' {
		index++
		if index >= len(t.text) {
			return t.exception("bad escape (end of pattern)", len(t.text)-1)
		}
		token = append(token, t.text[index])
	}
	t.index = index + 1
	t.next = token
	return nil
}

// tell is the token's position: the index after it, less its length.
func (t *sanitizeTokenizer) tell() int { return t.index - len(t.next) }

func (t *sanitizeTokenizer) exception(message string, position int) error {
	return newSanitizeTemplateError(message, t.text, position)
}

// error is re._parser.Tokenizer.error: the position is tell() - offset.
func (t *sanitizeTokenizer) error(message string, offset int) error {
	return t.exception(message, t.tell()-offset)
}

func (t *sanitizeTokenizer) get() ([]rune, error) {
	this := t.next
	if err := t.advance(); err != nil {
		return nil, err
	}
	return this, nil
}

func (t *sanitizeTokenizer) match(char rune) (bool, error) {
	if len(t.next) != 1 || t.next[0] != char {
		return false, nil
	}
	return true, t.advance()
}

func (t *sanitizeTokenizer) nextIn(chars string) bool {
	return len(t.next) == 1 && strings.ContainsRune(chars, t.next[0])
}

func (t *sanitizeTokenizer) getuntil(terminator rune, name string) (string, error) {
	var result []rune
	for {
		current := t.next
		if err := t.advance(); err != nil {
			return "", err
		}
		if current == nil {
			if len(result) == 0 {
				return "", t.error("missing "+name, 0)
			}
			return "", t.error("missing "+string(terminator)+", unterminated name", len(result))
		}
		if len(current) == 1 && current[0] == terminator {
			if len(result) == 0 {
				return "", t.error("missing "+name, 1)
			}
			break
		}
		result = append(result, current...)
	}
	return string(result), nil
}

// newSanitizeTemplateError is re.error.__init__(message, pattern, position):
// the message with the position and, when the pattern holds a newline, its
// line and column.
func newSanitizeTemplateError(message string, pattern []rune, position int) error {
	text := message + " at position " + strconv.Itoa(position)
	if strings.ContainsRune(string(pattern), '\n') {
		end, line, column := position, 1, position+1
		if end > len(pattern) {
			end = len(pattern)
		}
		for i := 0; i < end; i++ {
			if pattern[i] == '\n' {
				line++
			}
		}
		for i := end - 1; i >= 0; i-- {
			if pattern[i] == '\n' {
				column = position - i
				break
			}
		}
		text += " (line " + strconv.Itoa(line) + ", column " + strconv.Itoa(column) + ")"
	}
	return &mcpToolText{text: text}
}

// sanitizeDecimal is str.isdecimal() and str.isascii().
func sanitizeDecimal(text string) bool {
	if text == "" {
		return false
	}
	for _, r := range text {
		if r > 0x7f || !unicode.Is(unicode.Nd, r) {
			return false
		}
	}
	return true
}

// sanitizeIdentifier is str.isidentifier(): XID_Start then XID_Continue, which
// Go's unicode tables approximate.
func sanitizeIdentifier(text string) bool {
	if text == "" {
		return false
	}
	for i, r := range []rune(text) {
		if i == 0 {
			if r != '_' && !unicode.IsLetter(r) && !unicode.In(r, unicode.Nl) {
				return false
			}
			continue
		}
		if r != '_' && !unicode.IsLetter(r) && !unicode.IsDigit(r) && !unicode.In(r, unicode.Mn, unicode.Mc, unicode.Pc) {
			return false
		}
	}
	return true
}

// parseSanitizeTemplate is re._parser.parse_template(source, pattern) for a
// pattern without groups: only group 0 resolves.
func parseSanitizeTemplate(source string) (sanitizeTemplate, error) {
	tokenizer, err := newSanitizeTokenizer(source)
	if err != nil {
		return sanitizeTemplate{}, err
	}
	var parts []sanitizePart
	var literal strings.Builder
	addLiteral := func() {
		if literal.Len() > 0 {
			parts = append(parts, sanitizePart{text: literal.String()})
			literal.Reset()
		}
	}
	addGroup := func() {
		addLiteral()
		parts = append(parts, sanitizePart{match: true})
	}
	for {
		this, err := tokenizer.get()
		if err != nil {
			return sanitizeTemplate{}, err
		}
		if this == nil {
			break
		}
		if this[0] != '\\' {
			literal.WriteString(string(this))
			continue
		}
		c := this[1]
		switch {
		case c == 'g':
			matched, err := tokenizer.match('<')
			if err != nil {
				return sanitizeTemplate{}, err
			}
			if !matched {
				return sanitizeTemplate{}, tokenizer.error("missing <", 0)
			}
			name, err := tokenizer.getuntil('>', "group name")
			if err != nil {
				return sanitizeTemplate{}, err
			}
			if !sanitizeDecimal(name) {
				if !sanitizeIdentifier(name) {
					return sanitizeTemplate{}, tokenizer.error("bad character in group name "+dto.PythonRepr(name),
						len([]rune(name))+1)
				}
				return sanitizeTemplate{}, &mcpToolText{text: "unknown group name " + dto.PythonRepr(name)}
			}
			index, ok := new(big.Int).SetString(name, 10)
			if !ok || !index.IsInt64() || index.Int64() >= sanitizeTemplateGroups || index.Sign() > 0 {
				shown := name
				if ok {
					shown = index.String()
				}
				return sanitizeTemplate{}, tokenizer.error("invalid group reference "+shown, len([]rune(name))+1)
			}
			addGroup()
		case c == '0':
			digits := "0"
			if tokenizer.nextIn(sanitizeOctalDigits) {
				next, err := tokenizer.get()
				if err != nil {
					return sanitizeTemplate{}, err
				}
				digits += string(next)
				if tokenizer.nextIn(sanitizeOctalDigits) {
					if next, err = tokenizer.get(); err != nil {
						return sanitizeTemplate{}, err
					}
					digits += string(next)
				}
			}
			value, _ := strconv.ParseInt(digits, 8, 32)
			literal.WriteRune(rune(value & 0xff))
		case c >= '1' && c <= '9':
			digits := string(this)
			octal := false
			if tokenizer.nextIn(sanitizeDigits) {
				next, err := tokenizer.get()
				if err != nil {
					return sanitizeTemplate{}, err
				}
				digits += string(next)
				if strings.ContainsRune(sanitizeOctalDigits, c) && strings.ContainsRune(sanitizeOctalDigits, rune(digits[2])) &&
					tokenizer.nextIn(sanitizeOctalDigits) {
					if next, err = tokenizer.get(); err != nil {
						return sanitizeTemplate{}, err
					}
					digits += string(next)
					octal = true
					value, _ := strconv.ParseInt(digits[1:], 8, 32)
					if value > 0o377 {
						return sanitizeTemplate{}, tokenizer.error("octal escape value "+digits+" outside of range 0-0o377",
							len(digits))
					}
					literal.WriteRune(rune(value))
				}
			}
			if !octal {
				number, _ := strconv.Atoi(digits[1:])
				return sanitizeTemplate{}, tokenizer.error("invalid group reference "+strconv.Itoa(number), len(digits)-1)
			}
		default:
			if escaped, ok := sanitizeEscapes[c]; ok {
				literal.WriteRune(escaped)
				continue
			}
			if (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') {
				return sanitizeTemplate{}, tokenizer.error("bad escape "+string(this), len(this))
			}
			literal.WriteString(string(this))
		}
	}
	addLiteral()
	return sanitizeTemplate{parts: parts}, nil
}

// sanitizeReplacementTemplate parses re.sub's replacement: a text without a
// backslash has no escapes and never fails.
func sanitizeReplacementTemplate(replacement string) (sanitizeTemplate, error) {
	if !strings.ContainsRune(replacement, '\\') {
		return sanitizeTemplate{parts: []sanitizePart{{text: replacement}}}, nil
	}
	return parseSanitizeTemplate(replacement)
}

// sanitizeReplacementRuns is re.escape(replacement) + '+': the pattern of the
// second substitution, whose quantifier repeats the replacement's last
// character.
func sanitizeReplacementRuns(replacement string) string {
	points := []rune(replacement)
	var pattern strings.Builder
	for _, r := range points[:len(points)-1] {
		pattern.WriteString(regexp.QuoteMeta(string(r)))
	}
	pattern.WriteString(regexp.QuoteMeta(string(points[len(points)-1])) + "+")
	return pattern.String()
}

// sanitizeTextPython is sanitize_text(text, replacement) for the tool: the
// template is parsed before the text's type is read (re.sub compiles it
// first), and the result is the two substitutions, Python's strip() and the
// replacement's characters.
func sanitizeTextPython(text any, replacement string) (string, error) {
	template, err := sanitizeReplacementTemplate(replacement)
	if err != nil {
		return "", err
	}
	value, ok := text.(string)
	if !ok {
		return "", &mcpToolText{text: "expected string or bytes-like object, got '" + mcpValueTypeName(text) + "'"}
	}
	if value == "" {
		return value, nil
	}
	sanitized := unsafeFilename.ReplaceAllStringFunc(value, template.expand)
	if replacement != "" {
		runs, err := regexp.Compile(sanitizeReplacementRuns(replacement))
		if err != nil {
			return "", errBusinessMCPReplacement
		}
		sanitized = runs.ReplaceAllStringFunc(sanitized, template.expand)
	}
	sanitized = strings.TrimFunc(sanitized, pythonWhitespace)
	return strings.TrimFunc(sanitized, func(r rune) bool { return strings.ContainsRune(replacement, r) }), nil
}

// mcpValueTypeName is type(value).__name__ of a JSON tool value.
func mcpValueTypeName(value any) string {
	switch typed := value.(type) {
	case nil:
		return "NoneType"
	case bool:
		return "bool"
	case string:
		return "str"
	case json.Number:
		if strings.ContainsAny(string(typed), ".eE") {
			return "float"
		}
		return "int"
	case float64:
		return "float"
	case []any:
		return "list"
	case domain.RequestObject:
		return "dict"
	}
	return "object"
}

func pythonWhitespace(r rune) bool {
	switch r {
	case '\t', '\n', '\v', '\f', '\r', 0x1c, 0x1d, 0x1e, 0x1f, ' ', 0x85, 0xa0, 0x1680, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000:
		return true
	}
	return r >= 0x2000 && r <= 0x200a
}

// sanitize is tool_sanitize_text: {'result': sanitize_text(text, replacement)};
// a value it raises for is a tool exception with str(e).
func (t *BusinessMCPTools) sanitize(ctx context.Context, arguments domain.RequestObject) (any, error) {
	value, _ := arguments.Lookup("text")
	if mcpPythonFalsy(value) {
		return mcpObj("result", value), nil
	}
	replacement := "-"
	if raw, present := arguments.Lookup("replacement"); present {
		switch raw.(type) {
		case []any:
			return mcpRaisedResult(ctx, "unhashable type: 'list'")
		case domain.RequestObject:
			return mcpRaisedResult(ctx, "unhashable type: 'dict'")
		}
		text, ok := raw.(string)
		if !ok {
			return mcpRaisedResult(ctx, "decoding to str: need a bytes-like object, "+mcpValueTypeName(raw)+" found")
		}
		replacement = text
	}
	result, err := sanitizeTextPython(value, replacement)
	if err != nil {
		return mcpRaisedResult(ctx, err.Error())
	}
	return mcpObj("result", result), nil
}

// mcpPythonFalsy is `not value` for JSON values.
func mcpPythonFalsy(value any) bool {
	switch typed := value.(type) {
	case nil:
		return true
	case bool:
		return !typed
	case string:
		return typed == ""
	case json.Number:
		number, ok := new(big.Rat).SetString(string(typed))
		return ok && number.Sign() == 0
	case []any:
		return len(typed) == 0
	case domain.RequestObject:
		return len(typed) == 0
	}
	return false
}

// uuidArgument is UUIDField.to_python for a JSON argument: the canonical UUID
// text, or str(ValidationError) when Django's field rejects the value. An int
// (bool included) is UUID(int=...), a string UUID(hex=...); every other value
// is invalid, and the message carries its str().
func uuidArgument(value any) (string, string) {
	invalid := func() (string, string) {
		return "", `[` + dto.PythonRepr(`"`+dto.PythonStr(value)+`" non è uno UUID valido.`) + `]`
	}
	switch typed := value.(type) {
	case bool:
		if typed {
			return "00000000-0000-0000-0000-000000000001", ""
		}
		return "00000000-0000-0000-0000-000000000000", ""
	case json.Number:
		if strings.ContainsAny(string(typed), ".eE") {
			return invalid() // a float: UUID(int=float) raises ValueError
		}
		number, ok := new(big.Int).SetString(string(typed), 10)
		if !ok || number.Sign() < 0 || number.BitLen() > 128 {
			return invalid()
		}
		var raw [16]byte
		number.FillBytes(raw[:])
		return uuid.UUID(raw).String(), ""
	case string:
		text := strings.ReplaceAll(strings.ReplaceAll(typed, "urn:", ""), "uuid:", "")
		text = strings.ReplaceAll(strings.Trim(text, "{}"), "-", "")
		if len(text) != 32 || strings.Trim(strings.ToLower(text), "0123456789abcdef") != "" {
			return invalid()
		}
		parsed, err := uuid.Parse(strings.ToLower(text))
		if err != nil {
			return invalid()
		}
		return parsed.String(), ""
	}
	return invalid()
}

// userUUID is uuidArgument for a tool's user_id argument.
func userUUID(value any) (id string, message string) {
	return uuidArgument(value)
}

func (t *BusinessMCPTools) listReports(ctx context.Context, arguments domain.RequestObject) (any, error) {
	value, _ := arguments.Lookup("user_id")
	if mcpPythonFalsy(value) {
		return mcpErrorResult("user_id is required to list reports"), nil
	}
	user, message := userUUID(value)
	if message != "" {
		markMCPRaised(ctx) // UUIDField.to_python raises in the query
		return mcpErrorResult(message), nil
	}
	rows, err := t.store.SavedReports(ctx, t.association, user)
	if err != nil {
		return nil, err
	}
	reports := make([]any, 0, len(rows))
	for _, row := range rows {
		reports = append(reports, mcpObj("saved_report_id", row.ID, "name", row.Name, "description", row.Description,
			"tool_name", row.ToolName, "updated_at", dto.PythonIsoformat(row.UpdatedAt)))
	}
	return mcpObj("reports", reports, "count", json.Number(strconv.Itoa(len(reports)))), nil
}

func (t *BusinessMCPTools) saveReport(ctx context.Context, arguments domain.RequestObject) (any, error) {
	lookup := func(key string) any { value, _ := arguments.Lookup(key); return value }
	// CharField/TextField.to_python: a string stays, None stays None (the NOT
	// NULL column rejects it) and anything else is str().
	storedText := func(value any) *string {
		if value == nil {
			return nil
		}
		text := dto.PythonStr(value)
		return &text
	}
	name := storedText(lookup("name"))
	tool := storedText(lookup("tool_name"))
	description := storedText("")
	if value, present := arguments.Lookup("description"); present {
		description = storedText(value)
	}
	params := lookup("params")
	config := lookup("ui_config")
	if !mcpTruthy(config) {
		config = domain.RequestObject{} // `ui_config or {}`
	}
	value := lookup("user_id")
	if mcpPythonFalsy(value) {
		return mcpErrorResult("user_id is required to save a report"), nil
	}
	user, message := userUUID(value)
	if message != "" {
		markMCPRaised(ctx) // UUIDField.to_python raises in the query
		return mcpErrorResult(message), nil
	}
	exists, err := t.store.ActiveUserExists(ctx, user)
	if err != nil {
		return nil, err
	}
	if !exists {
		return mcpErrorResult("User not found"), nil
	}
	// psycopg2 rejects a NUL in a text parameter before the INSERT; PostgreSQL
	// checks the column lengths as it forms the tuple, in column order.
	for _, text := range []*string{name, description, tool} {
		if text != nil && strings.ContainsRune(*text, 0) {
			return mcpRaisedResult(ctx, "A string literal cannot contain NUL (0x00) characters.")
		}
	}
	if name != nil && utf8.RuneCountInString(*name) > 200 {
		return mcpRaisedResult(ctx, "value too long for type character varying(200)\n")
	}
	if tool != nil && utf8.RuneCountInString(*tool) > 100 {
		return mcpRaisedResult(ctx, "value too long for type character varying(100)\n")
	}
	// jsonb rejects a NUL escape and the Infinity and NaN literals json.dumps
	// writes; the NOT NULL columns reject None.
	if name == nil || description == nil || tool == nil || params == nil ||
		jsonContainsNUL(params) || jsonContainsNUL(config) || jsonContainsNonFinite(params) || jsonContainsNonFinite(config) {
		return nil, newBusinessToolRaised("", nil)
	}
	if exists, err = t.store.AssociationExists(ctx, t.association); err != nil || !exists {
		if err == nil {
			err = newBusinessToolRaised("", nil) // ForeignKeyViolation of the INSERT
		}
		return nil, err
	}
	id := uuid.NewString()
	if err = t.store.InsertSavedReport(ctx, id, t.association, user, domain.SavedReportWrite{Name: *name, ToolName: *tool,
		Description: *description, Params: string(dto.PythonJSONDumps(params)), UIConfig: string(dto.PythonJSONDumps(config)),
		At: t.now().UTC().Truncate(time.Microsecond)}); err != nil {
		return nil, err
	}
	return mcpObj("saved_report_id", id, "name", lookup("name"), "message", `Report "`+dto.PythonStr(lookup("name"))+`" salvato.`), nil
}

// jsonContainsNonFinite reports a number Python's json.dumps writes as
// Infinity or NaN: PostgreSQL's jsonb input rejects both.
func jsonContainsNonFinite(value any) bool {
	switch typed := value.(type) {
	case float64:
		return math.IsInf(typed, 0) || math.IsNaN(typed)
	case json.Number:
		if strings.ContainsAny(string(typed), ".eE") {
			number, _ := strconv.ParseFloat(string(typed), 64)
			return math.IsInf(number, 0) || math.IsNaN(number)
		}
	case domain.RequestObject:
		for _, field := range typed {
			if jsonContainsNonFinite(field.Value) {
				return true
			}
		}
	case []any:
		for _, item := range typed {
			if jsonContainsNonFinite(item) {
				return true
			}
		}
	}
	return false
}

// jsonContainsNUL reports a NUL character in any string of a JSON value
// (PostgreSQL rejects it in text and jsonb).
func jsonContainsNUL(value any) bool {
	switch typed := value.(type) {
	case string:
		return strings.ContainsRune(typed, 0)
	case domain.RequestObject:
		for _, field := range typed {
			if jsonContainsNUL(field.Name) || jsonContainsNUL(field.Value) {
				return true
			}
		}
	case []any:
		for _, item := range typed {
			if jsonContainsNUL(item) {
				return true
			}
		}
	}
	return false
}
