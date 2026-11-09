package service

import (
	"context"
	"encoding/json"
	"errors"
	"math/big"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

// ErrBusinessMCPNotPorted refuses a business MCP tool call (or argument)
// whose Django behaviour is not ported, before any query or write.
var ErrBusinessMCPNotPorted = errors.New("business MCP tool input not ported")

// BusinessMCPStore is the persistence the ported tool functions use.
type BusinessMCPStore interface {
	SavedReports(ctx context.Context, association, creator string) ([]domain.SavedReport, error)
	InsertSavedReport(ctx context.Context, id, association, creator string, write domain.SavedReportWrite) error
	// ActiveUserExists is User.objects.filter(user_id=id).exists() (not deleted).
	ActiveUserExists(ctx context.Context, id string) (bool, error)
	AssociationExists(ctx context.Context, id string) (bool, error)
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
}

func NewBusinessMCPTools(store BusinessMCPStore, association string, now func() time.Time) *BusinessMCPTools {
	return &BusinessMCPTools{store: store, association: association, now: now}
}

// Execute dispatches like create_mcp_server's call_tool for the ported
// tools; arguments are the parsed call arguments in their JSON order and
// the returned value is the JSON result Django serialises.
func (t *BusinessMCPTools) Execute(ctx context.Context, name string, arguments domain.RequestObject) (any, error) {
	switch name {
	case "sanitize_text":
		return t.sanitize(arguments)
	case "list_reports":
		return t.listReports(ctx, arguments)
	case "save_report":
		return t.saveReport(ctx, arguments)
	}
	return nil, ErrBusinessMCPNotPorted
}

func errorResult(message string) map[string]any { return map[string]any{"error": message} }

// unsafeFilename is _UNSAFE_FILENAME_RE.
var unsafeFilename = regexp.MustCompile(`[<>:"/\\|?*\x00-\x1f]`)

// SanitizeText is server.sanitize_text(text, replacement). A replacement
// containing a backslash is a re.sub template with escapes: not ported.
func SanitizeText(text, replacement string) (string, error) {
	if text == "" {
		return text, nil
	}
	if strings.Contains(replacement, `\`) {
		return "", ErrBusinessMCPNotPorted
	}
	sanitized := unsafeFilename.ReplaceAllLiteralString(text, replacement)
	if replacement != "" {
		// re.escape(replacement) + '+': the quantifier repeats the last character.
		last, size := utf8.DecodeLastRuneInString(replacement)
		runs := regexp.MustCompile(regexp.QuoteMeta(replacement[:len(replacement)-size]) + "(?:" + regexp.QuoteMeta(string(last)) + ")+")
		sanitized = runs.ReplaceAllLiteralString(sanitized, replacement)
	}
	sanitized = strings.TrimFunc(sanitized, pythonWhitespace)
	return strings.Trim(sanitized, replacement), nil
}

func pythonWhitespace(r rune) bool {
	switch r {
	case '\t', '\n', '\v', '\f', '\r', 0x1c, 0x1d, 0x1e, 0x1f, ' ', 0x85, 0xa0, 0x1680, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000:
		return true
	}
	return r >= 0x2000 && r <= 0x200a
}

func (t *BusinessMCPTools) sanitize(arguments domain.RequestObject) (any, error) {
	value, _ := arguments.Lookup("text")
	text, ok := value.(string)
	if !ok {
		return nil, ErrBusinessMCPNotPorted
	}
	replacement := "-"
	if value, present := arguments.Lookup("replacement"); present {
		if replacement, ok = value.(string); !ok {
			return nil, ErrBusinessMCPNotPorted
		}
	}
	result, err := SanitizeText(text, replacement)
	if err != nil {
		return nil, err
	}
	return map[string]any{"result": result}, nil
}

// pythonFalsy is `not value` for JSON values.
func pythonFalsy(value any) bool {
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

// userUUID is UUIDField.to_python for a user_id argument: a string is
// UUID(hex=...), an int (bool included) UUID(int=...). Invalid strings give
// the ValidationError text str(e) renders; other types are not ported.
func userUUID(value any) (id string, message string, err error) {
	invalid := func(text string) (string, string, error) {
		if strings.ContainsAny(text, `'\`) || strings.IndexFunc(text, func(r rune) bool { return r < 0x20 || r == 0x7f }) >= 0 {
			return "", "", ErrBusinessMCPNotPorted // repr() quoting not ported
		}
		return "", `['"` + text + `" non è uno UUID valido.']`, nil
	}
	switch typed := value.(type) {
	case bool:
		if typed {
			return "00000000-0000-0000-0000-000000000001", "", nil
		}
		return "00000000-0000-0000-0000-000000000000", "", nil
	case json.Number:
		if strings.ContainsAny(string(typed), ".eE") {
			return "", "", ErrBusinessMCPNotPorted
		}
		number, ok := new(big.Int).SetString(string(typed), 10)
		if !ok || number.Sign() < 0 || number.BitLen() > 128 {
			return invalid(string(typed))
		}
		var raw [16]byte
		number.FillBytes(raw[:])
		return uuid.UUID(raw).String(), "", nil
	case string:
		text := strings.ReplaceAll(strings.ReplaceAll(typed, "urn:", ""), "uuid:", "")
		text = strings.ReplaceAll(strings.Trim(text, "{}"), "-", "")
		if len(text) != 32 || strings.Trim(strings.ToLower(text), "0123456789abcdef") != "" {
			return invalid(typed)
		}
		parsed, parseErr := uuid.Parse(strings.ToLower(text))
		if parseErr != nil {
			return invalid(typed)
		}
		return parsed.String(), "", nil
	}
	return "", "", ErrBusinessMCPNotPorted
}

func (t *BusinessMCPTools) listReports(ctx context.Context, arguments domain.RequestObject) (any, error) {
	value, _ := arguments.Lookup("user_id")
	if pythonFalsy(value) {
		return errorResult("user_id is required to list reports"), nil
	}
	user, message, err := userUUID(value)
	if err != nil {
		return nil, err
	}
	if message != "" {
		return errorResult(message), nil
	}
	rows, err := t.store.SavedReports(ctx, t.association, user)
	if err != nil {
		return nil, err
	}
	reports := make([]any, 0, len(rows))
	for _, row := range rows {
		reports = append(reports, map[string]any{"saved_report_id": row.ID, "name": row.Name, "description": row.Description,
			"tool_name": row.ToolName, "updated_at": dto.PythonIsoformat(row.UpdatedAt)})
	}
	return map[string]any{"reports": reports, "count": len(reports)}, nil
}

func (t *BusinessMCPTools) saveReport(ctx context.Context, arguments domain.RequestObject) (any, error) {
	lookup := func(key string) any { value, _ := arguments.Lookup(key); return value }
	name, okName := lookup("name").(string)
	tool, okTool := lookup("tool_name").(string)
	params, okParams := lookup("params").(domain.RequestObject)
	description, okDescription := "", true
	if value, present := arguments.Lookup("description"); present {
		description, okDescription = value.(string)
	}
	config, okConfig := domain.RequestObject{}, true
	if value, present := arguments.Lookup("ui_config"); present {
		config, okConfig = value.(domain.RequestObject) // `ui_config or {}`: an empty object stays {}
	}
	if !okName || !okTool || !okParams || !okDescription || !okConfig {
		// The MCP schema rejects these before the function runs.
		return nil, ErrBusinessMCPNotPorted
	}
	value := lookup("user_id")
	if pythonFalsy(value) {
		return errorResult("user_id is required to save a report"), nil
	}
	user, message, err := userUUID(value)
	if err != nil {
		return nil, err
	}
	if message != "" {
		return errorResult(message), nil
	}
	exists, err := t.store.ActiveUserExists(ctx, user)
	if err != nil {
		return nil, err
	}
	if !exists {
		return errorResult("User not found"), nil
	}
	// Column limits, NUL characters and a missing association are database
	// errors whose driver text Django returns; they are not ported.
	if utf8.RuneCountInString(name) > 200 || utf8.RuneCountInString(tool) > 100 ||
		jsonContainsNUL(name+tool+description) || jsonContainsNUL(params) || jsonContainsNUL(config) {
		return nil, ErrBusinessMCPNotPorted
	}
	if exists, err = t.store.AssociationExists(ctx, t.association); err != nil || !exists {
		if err == nil {
			err = ErrBusinessMCPNotPorted
		}
		return nil, err
	}
	id := uuid.NewString()
	if err = t.store.InsertSavedReport(ctx, id, t.association, user, domain.SavedReportWrite{Name: name, ToolName: tool,
		Description: description, Params: string(dto.PythonJSONDumps(params)), UIConfig: string(dto.PythonJSONDumps(config)),
		At: t.now().UTC().Truncate(time.Microsecond)}); err != nil {
		return nil, err
	}
	return map[string]any{"saved_report_id": id, "name": name, "message": `Report "` + name + `" salvato.`}, nil
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
