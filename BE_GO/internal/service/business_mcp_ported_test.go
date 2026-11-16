package service_test

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"math"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
)

// ---------------------------------------------------------------------------
// Fakes
// ---------------------------------------------------------------------------

// portedStore answers the business tools' statements from one function and
// records what the tools write.
type portedStore struct {
	query       func(sql string, args []any) [][]*string
	integration *string
	activeUser  bool
	association bool
	writeID     string
	writeUser   string
	write       *domain.SavedReportWrite
	statements  []string
	parameters  [][]any
	runErr      error
}

func (s *portedStore) SavedReports(context.Context, string, string) ([]domain.SavedReport, error) {
	return nil, nil
}

func (s *portedStore) InsertSavedReport(_ context.Context, id, _, user string, write domain.SavedReportWrite) error {
	s.writeID, s.writeUser, s.write = id, user, &write
	return nil
}

func (s *portedStore) ActiveUserExists(context.Context, string) (bool, error) {
	return s.activeUser, nil
}

func (s *portedStore) AssociationExists(context.Context, string) (bool, error) {
	return s.association, nil
}

func (s *portedStore) ReportIntegration(context.Context) (bool, *string, error) {
	return s.integration != nil, s.integration, nil
}

func (s *portedStore) RunReportStatements(_ context.Context, _ int64, statements []domain.ReportStatement) ([][][]*string, error) {
	results := make([][][]*string, 0, len(statements))
	if s.runErr != nil {
		return nil, s.runErr
	}
	for _, statement := range statements {
		s.statements, s.parameters = append(s.statements, statement.SQL), append(s.parameters, statement.Args)
		if s.query == nil {
			results = append(results, nil)
			continue
		}
		results = append(results, s.query(statement.SQL, statement.Args))
	}
	return results, nil
}

const portedAssociation = "a1000000-0000-4000-8000-000000000001"

const portedUser = "a1000000-0000-4000-8000-000000000002"

func portedTools(store *portedStore) *service.BusinessMCPTools {
	return service.NewBusinessMCPTools(store, portedAssociation, func() time.Time {
		return time.Date(2026, 11, 16, 9, 30, 0, 0, time.UTC)
	})
}

func portedExecute(t *testing.T, tools *service.BusinessMCPTools, name string, arguments domain.RequestObject) (any, bool) {
	t.Helper()
	raised := false
	result, err := tools.Execute(service.WithMCPRaised(context.Background(), &raised), name, arguments)
	if err != nil {
		t.Fatalf("%s: %v", name, err)
	}
	return result, raised
}

func portedParse(t *testing.T, arguments string) domain.RequestObject {
	t.Helper()
	parsed, err := dto.ParseReferenceJSON([]byte(arguments))
	if err != nil {
		t.Fatal(err)
	}
	return parsed.(domain.RequestObject)
}

func portedObject(pairs ...any) domain.RequestObject {
	object := domain.RequestObject{}
	for i := 0; i < len(pairs); i += 2 {
		object = append(object, domain.RequestField{Name: pairs[i].(string), Value: pairs[i+1]})
	}
	return object
}

// portedError checks a tool result that stands for an exception with text.
func portedError(t *testing.T, result any, raised bool, text string) {
	t.Helper()
	if !raised {
		t.Fatalf("the exception is not marked raised: %s", string(dto.PythonJSONDumps(result)))
	}
	object, ok := result.(domain.RequestObject)
	if !ok {
		t.Fatalf("result %T", result)
	}
	message, present := object.Lookup("error")
	if !present || len(object) != 1 {
		t.Fatalf("result %s", string(dto.PythonJSONDumps(result)))
	}
	if message != text {
		t.Fatalf("error text %q, want %q", message, text)
	}
}

// ---------------------------------------------------------------------------
// sanitize_text (item 7)
// ---------------------------------------------------------------------------

func TestSanitizeTextTool(t *testing.T) {
	number := func(text string) json.Number { return json.Number(text) }
	cases := []struct {
		name    string
		text    any
		repl    any
		present bool
		want    string // the {"result": ...} or {"error": ...} result
	}{
		{name: "dash", text: "a/b", want: `{"result": "a-b"}`},
		{name: "collapse", text: "a//b", want: `{"result": "a-b"}`},
		{name: "trim", text: " a b ", want: `{"result": "a b"}`},
		{name: "only-unsafe", text: "---", want: `{"result": ""}`},
		{name: "zero-text", text: number("0"), want: `{"result": 0}`},
		{name: "null-text", text: nil, present: true, want: `{"result": null}`},
		{name: "false-text", text: false, want: `{"result": false}`},
		{name: "empty-text", text: "", want: `{"result": ""}`},
		{name: "empty-list-text", text: []any{}, want: `{"result": []}`},
		{name: "template-newline", text: "a/b", repl: `\n`, present: true, want: `{"result": "a\nb"}`},
		{name: "group-reference", text: "a/b", repl: `\1`, present: true,
			want: `{"error": "invalid group reference 1 at position 1"}`},
		{name: "whole-match", text: "a/b", repl: `\g<0>`, present: true, want: `{"result": "a/b"}`},
		{name: "bad-escape", text: "ab", repl: `\q`, present: true,
			want: `{"error": "bad escape \\q at position 0"}`},
		{name: "escaped-backslash", text: "a/b", repl: `\\`, present: true, want: `{"result": "a\\b"}`},
		{name: "nul-escape", text: "a/b", repl: `\0`, present: true, want: `{"result": "a\u0000b"}`},
		{name: "hex-escape", text: "a/b", repl: `\x41`, present: true,
			want: `{"error": "bad escape \\x at position 0"}`},
		{name: "unicode-escape", text: "a/b", repl: `\u0041`, present: true,
			want: `{"error": "bad escape \\u at position 0"}`},
		{name: "bell", text: "a/b", repl: `\a`, present: true, want: `{"result": "\u0007b"}`},
		{name: "named-group", text: "a/b", repl: `\g<name>`, present: true,
			want: `{"error": "unknown group name 'name'"}`},
		{name: "group-position", text: "a/b", repl: `\g<1>`, present: true,
			want: `{"error": "invalid group reference 1 at position 3"}`},
		{name: "lone-backslash", text: "a/b", repl: `\`, present: true,
			want: `{"error": "bad escape (end of pattern) at position 0"}`},
		{name: "trailing-backslash", text: "a/b", repl: `x\`, present: true,
			want: `{"error": "bad escape (end of pattern) at position 1"}`},
		{name: "bad-group-name", text: "a/b", repl: `\g<0x>`, present: true,
			want: `{"error": "bad character in group name '0x' at position 3"}`},
		{name: "named-unicode-escape", text: "a/b", repl: `\N{BULLET}`, present: true,
			want: `{"error": "bad escape \\N at position 0"}`},
		{name: "octal-three", text: "a/b", repl: `\012`, present: true, want: `{"result": "a\nb"}`},
		{name: "tab-runs", text: "a//b", repl: `\t`, present: true, want: `{"result": "a\t\tb"}`},
		{name: "list-text", text: []any{number("1")}, want: `{"error": "expected string or bytes-like object, got 'list'"}`},
		{name: "int-text", text: number("5"), want: `{"error": "expected string or bytes-like object, got 'int'"}`},
		{name: "int-replacement", text: "a/b", repl: number("5"), present: true,
			want: `{"error": "decoding to str: need a bytes-like object, int found"}`},
		{name: "null-replacement", text: "a/b", repl: nil, present: true,
			want: `{"error": "decoding to str: need a bytes-like object, NoneType found"}`},
		{name: "list-replacement", text: "a/b", repl: []any{"x"}, present: true,
			want: `{"error": "unhashable type: 'list'"}`},
		{name: "dict-replacement", text: "a/b", repl: portedObject("a", number("1")), present: true,
			want: `{"error": "unhashable type: 'dict'"}`},
		{name: "bool-replacement", text: "a/b", repl: true, present: true,
			want: `{"error": "decoding to str: need a bytes-like object, bool found"}`},
		{name: "float-replacement", text: "a/b", repl: number("1.5"), present: true,
			want: `{"error": "decoding to str: need a bytes-like object, float found"}`},
	}
	for _, item := range cases {
		t.Run(item.name, func(t *testing.T) {
			arguments := portedObject("text", item.text)
			if item.present {
				arguments = append(arguments, domain.RequestField{Name: "replacement", Value: item.repl})
			}
			result, raised := portedExecute(t, portedTools(&portedStore{}), "sanitize_text", arguments)
			if got := string(dto.PythonJSONDumps(result)); got != item.want {
				t.Fatalf("result %s, want %s", got, item.want)
			}
			wantRaised := strings.HasPrefix(item.want, `{"error"`)
			if raised != wantRaised {
				t.Fatalf("raised %v", raised)
			}
		})
	}
}

// ---------------------------------------------------------------------------
// userUUID (item 8)
// ---------------------------------------------------------------------------

func TestListReportsInvalidUser(t *testing.T) {
	number := func(text string) json.Number { return json.Number(text) }
	cases := []struct {
		name  string
		value any
		want  string
	}{
		{"float", number("1.5"), `['"1.5" non è uno UUID valido.']`},
		{"float-exponent", number("1e5"), `['"100000.0" non è uno UUID valido.']`},
		{"float-overflow", number("1e400"), `['"inf" non è uno UUID valido.']`},
		{"list", []any{number("1")}, `['"[1]" non è uno UUID valido.']`},
		{"quote", "a'b", `['"a\'b" non è uno UUID valido.']`},
		{"newline", "a\nb", `['"a\nb" non è uno UUID valido.']`},
		{"dict", portedObject("a", number("1")), `['"{\'a\': 1}" non è uno UUID valido.']`},
		{"nan", math.NaN(), `['"nan" non è uno UUID valido.']`},
		{"inf", math.Inf(1), `['"inf" non è uno UUID valido.']`},
		{"negative-inf", math.Inf(-1), `['"-inf" non è uno UUID valido.']`},
		{"backslash", `a\b`, `['"a\\b" non è uno UUID valido.']`},
		{"control", "a\x01b", `['"a\x01b" non è uno UUID valido.']`},
		{"negative-int", number("-1"), `['"-1" non è uno UUID valido.']`},
		{"wide-int", number("10000000000000000000000000000000000000000"),
			`['"10000000000000000000000000000000000000000" non è uno UUID valido.']`},
		{"double-quote", `a"b`, `['"a"b" non è uno UUID valido.']`},
		{"non-ascii", "é", `['"é" non è uno UUID valido.']`},
	}
	for _, item := range cases {
		t.Run(item.name, func(t *testing.T) {
			result, raised := portedExecute(t, portedTools(&portedStore{}), "list_reports",
				portedObject("user_id", item.value))
			portedError(t, result, raised, item.want)
		})
	}
}

// ---------------------------------------------------------------------------
// saveReport (item 9)
// ---------------------------------------------------------------------------

func TestSaveReport(t *testing.T) {
	number := func(text string) json.Number { return json.Number(text) }
	store := func() *portedStore {
		return &portedStore{activeUser: true, association: true}
	}
	saved := []struct {
		name       string
		arguments  domain.RequestObject
		storedName string
		storedTool string
		storedDesc string
		params     string
		config     string
		response   string
	}{
		{
			name:       "int-name",
			arguments:  portedObject("name", number("5"), "tool_name", "export_data", "params", portedObject()),
			storedName: "5", storedTool: "export_data", storedDesc: "", params: "{}", config: "{}",
			response: `"name": 5, "message": "Report \"5\" salvato."`,
		},
		{
			name:       "int-tool-and-list-params",
			arguments:  portedObject("name", "Etichette", "tool_name", number("5"), "params", []any{number("1")}),
			storedName: "Etichette", storedTool: "5", storedDesc: "", params: "[1]", config: "{}",
			response: `"name": "Etichette", "message": "Report \"Etichette\" salvato."`,
		},
		{
			name:       "string-params",
			arguments:  portedObject("name", "Etichette", "tool_name", "export_data", "params", "abc"),
			storedName: "Etichette", storedTool: "export_data", storedDesc: "", params: `"abc"`, config: "{}",
			response: `"name": "Etichette", "message": "Report \"Etichette\" salvato."`,
		},
		{
			name: "empty-list-config",
			arguments: portedObject("name", "Etichette", "tool_name", "export_data", "params", portedObject(),
				"ui_config", []any{}),
			storedName: "Etichette", storedTool: "export_data", storedDesc: "", params: "{}", config: "{}",
			response: `"name": "Etichette", "message": "Report \"Etichette\" salvato."`,
		},
		{
			name: "string-config",
			arguments: portedObject("name", "Etichette", "tool_name", "export_data", "params", portedObject(),
				"ui_config", "abc"),
			storedName: "Etichette", storedTool: "export_data", storedDesc: "", params: "{}", config: `"abc"`,
			response: `"name": "Etichette", "message": "Report \"Etichette\" salvato."`,
		},
		{
			name: "int-description",
			arguments: portedObject("name", "Etichette", "tool_name", "export_data", "params", portedObject(),
				"description", number("5")),
			storedName: "Etichette", storedTool: "export_data", storedDesc: "5", params: "{}", config: "{}",
			response: `"name": "Etichette", "message": "Report \"Etichette\" salvato."`,
		},
	}
	for _, item := range saved {
		t.Run(item.name, func(t *testing.T) {
			fake := store()
			arguments := append(domain.RequestObject{}, item.arguments...)
			arguments = append(arguments, domain.RequestField{Name: "user_id", Value: portedUser})
			result, raised := portedExecute(t, portedTools(fake), "save_report", arguments)
			if raised {
				t.Fatalf("raised: %s", string(dto.PythonJSONDumps(result)))
			}
			object := result.(domain.RequestObject)
			id, _ := object.Lookup("saved_report_id")
			if id != fake.writeID || fake.writeID == "" {
				t.Fatalf("saved_report_id %v, write %q", id, fake.writeID)
			}
			if fake.writeUser != portedUser {
				t.Fatalf("creator %q", fake.writeUser)
			}
			if fake.write == nil || fake.write.Name != item.storedName || fake.write.ToolName != item.storedTool ||
				fake.write.Description != item.storedDesc || string(fake.write.Params) != item.params ||
				string(fake.write.UIConfig) != item.config {
				t.Fatalf("write %+v", fake.write)
			}
			dumps := string(dto.PythonJSONDumps(result))
			if !strings.Contains(dumps, item.response) {
				t.Fatalf("result %s", dumps)
			}
			if !strings.HasPrefix(dumps, `{"saved_report_id": `) {
				t.Fatalf("key order %s", dumps)
			}
		})
	}

	rejected := []struct {
		name      string
		arguments domain.RequestObject
		text      string // a raised text, or "" for ErrBusinessToolRaised
	}{
		{
			name:      "null-description",
			arguments: portedObject("name", "Etichette", "tool_name", "export_data", "params", portedObject(), "description", nil),
		},
		{
			name:      "null-name",
			arguments: portedObject("name", nil, "tool_name", "export_data", "params", portedObject()),
		},
		{
			name:      "null-tool",
			arguments: portedObject("name", "Etichette", "tool_name", nil, "params", portedObject()),
		},
		{
			name:      "null-params",
			arguments: portedObject("name", "Etichette", "tool_name", "export_data", "params", nil),
		},
		{
			name: "nan-params",
			arguments: portedObject("name", "Etichette", "tool_name", "export_data",
				"params", portedObject("a", math.NaN())),
		},
		{
			name: "infinite-config",
			arguments: portedObject("name", "Etichette", "tool_name", "export_data", "params", portedObject(),
				"ui_config", portedObject("a", number("1e400"))),
		},
		{
			name: "nul-in-params",
			arguments: portedObject("name", "Etichette", "tool_name", "export_data",
				"params", portedObject("a", "x\x00y")),
		},
		{
			name:      "long-name",
			arguments: portedObject("name", strings.Repeat("x", 201), "tool_name", "export_data", "params", portedObject()),
			text:      "value too long for type character varying(200)\n",
		},
		{
			name: "long-tool",
			arguments: portedObject("name", "Etichette", "tool_name", strings.Repeat("x", 101),
				"params", portedObject()),
			text: "value too long for type character varying(100)\n",
		},
		{
			name:      "nul-name",
			arguments: portedObject("name", "a\x00b", "tool_name", "export_data", "params", portedObject()),
			text:      "A string literal cannot contain NUL (0x00) characters.",
		},
		{
			name: "nul-description-before-length",
			arguments: portedObject("name", strings.Repeat("x", 201), "tool_name", "export_data",
				"params", portedObject(), "description", "a\x00b"),
			text: "A string literal cannot contain NUL (0x00) characters.",
		},
	}
	for _, item := range rejected {
		t.Run(item.name, func(t *testing.T) {
			arguments := append(domain.RequestObject{}, item.arguments...)
			arguments = append(arguments, domain.RequestField{Name: "user_id", Value: portedUser})
			raised := false
			result, err := portedTools(store()).Execute(service.WithMCPRaised(context.Background(), &raised), "save_report", arguments)
			if item.text != "" {
				if err != nil {
					t.Fatalf("call: %v", err)
				}
				portedError(t, result, raised, item.text)
				return
			}
			if !errors.Is(err, service.ErrBusinessToolRaised) {
				t.Fatalf("expected ErrBusinessToolRaised, got %v (%v)", err, result)
			}
		})
	}

	t.Run("missing-association", func(t *testing.T) {
		fake := &portedStore{activeUser: true}
		arguments := portedObject("name", "Etichette", "tool_name", "export_data", "params", portedObject(),
			"user_id", portedUser)
		if _, err := portedTools(fake).Execute(context.Background(), "save_report", arguments); !errors.Is(err, service.ErrBusinessToolRaised) {
			t.Fatalf("expected ErrBusinessToolRaised, got %v", err)
		}
	})

	t.Run("missing-user", func(t *testing.T) {
		// A missing user is a normal {'error': ...} result, not an exception.
		result, _ := portedExecute(t, portedTools(&portedStore{association: true}), "save_report",
			portedObject("name", "Etichette", "tool_name", "export_data", "params", portedObject(), "user_id", portedUser))
		if got := string(dto.PythonJSONDumps(result)); got != `{"error": "User not found"}` {
			t.Fatalf("result %s", got)
		}
	})
}

// ---------------------------------------------------------------------------
// export_multi_sheet argument types (item 11)
// ---------------------------------------------------------------------------

func TestExportMultiSheetSheetsTypes(t *testing.T) {
	number := func(text string) json.Number { return json.Number(text) }
	cases := []struct {
		name  string
		value any
		want  string
	}{
		{"int", number("5"), "'int' object is not iterable"},
		{"float", number("1.5"), "'float' object is not iterable"},
		{"true", true, "'bool' object is not iterable"},
		{"string", "abc", "'str' object has no attribute 'get'"},
		{"dict", portedObject("model_name", "Tags"), "'str' object has no attribute 'get'"},
		{"int-item", []any{number("5")}, "'int' object has no attribute 'get'"},
		{"null-item", []any{nil}, "'NoneType' object has no attribute 'get'"},
		{"string-item", []any{"x"}, "'str' object has no attribute 'get'"},
		{"list-item", []any{[]any{number("1")}}, "'list' object has no attribute 'get'"},
	}
	for _, item := range cases {
		t.Run(item.name, func(t *testing.T) {
			arguments := portedObject("sheets", item.value)
			result, raised := portedExecute(t, portedTools(&portedStore{}), "export_multi_sheet", arguments)
			portedError(t, result, raised, item.want)
		})
	}
}

// ---------------------------------------------------------------------------
// export_multi_sheet files (item 12)
// ---------------------------------------------------------------------------

func sheetStore() *portedStore {
	return &portedStore{query: func(sql string, _ []any) [][]*string {
		text := func(value string) *string { return &value }
		if strings.Contains(sql, "COUNT") {
			return [][]*string{{text("2")}}
		}
		return [][]*string{{text("WSAgent Alfa")}, {text("WSAgent Beta")}}
	}}
}

func TestExportMultiSheetFiles(t *testing.T) {
	sheet := func(title any) any {
		spec := portedObject("model_name", "Tags", "fields", []any{"tag_name"})
		if title != nil {
			spec = append(spec, domain.RequestField{Name: "title", Value: title})
		}
		return []any{spec}
	}
	t.Run("null-filename", func(t *testing.T) {
		arguments := portedObject("filename", nil, "sheets", sheet("Etichette"))
		result, raised := portedExecute(t, portedTools(sheetStore()), "export_multi_sheet", arguments)
		if raised {
			t.Fatalf("raised: %s", string(dto.PythonJSONDumps(result)))
		}
		object := result.(domain.RequestObject)
		filename, _ := object.Lookup("filename")
		if filename != "None.xlsx" {
			t.Fatalf("filename %v", filename)
		}
		info, _ := object.Lookup("save_info")
		params, _ := info.(domain.RequestObject).Lookup("params")
		if value, _ := params.(domain.RequestObject).Lookup("filename"); value != nil {
			t.Fatalf("save_info filename %v", value)
		}
		name, _ := info.(domain.RequestObject).Lookup("name")
		if name != "None - 16/11/2026" {
			t.Fatalf("save_info name %v", name)
		}
		description, _ := info.(domain.RequestObject).Lookup("description")
		if description != "Export multi-foglio None - 2 righe, 1 fogli" {
			t.Fatalf("save_info description %v", description)
		}
	})
	t.Run("zero-filename", func(t *testing.T) {
		arguments := portedObject("filename", json.Number("0"), "sheets", sheet("Etichette"))
		result, raised := portedExecute(t, portedTools(sheetStore()), "export_multi_sheet", arguments)
		if raised {
			t.Fatalf("raised: %s", string(dto.PythonJSONDumps(result)))
		}
		if filename, _ := result.(domain.RequestObject).Lookup("filename"); filename != "0.xlsx" {
			t.Fatalf("filename %v", filename)
		}
	})
	t.Run("int-filename", func(t *testing.T) {
		arguments := portedObject("filename", json.Number("5"), "sheets", sheet("Etichette"))
		result, raised := portedExecute(t, portedTools(sheetStore()), "export_multi_sheet", arguments)
		portedError(t, result, raised, "expected string or bytes-like object, got 'int'")
	})

	titles := []struct {
		name  string
		specs []any
		want  string
	}{
		{"int-title-first", []any{portedObject("model_name", "Tags", "fields", []any{"tag_name"}, "title", json.Number("5"))},
			"At least one sheet must be visible"},
		{"null-title-first", []any{portedObject("model_name", "Tags", "fields", []any{"tag_name"}, "title", nil)},
			"At least one sheet must be visible"},
		{"list-title-first", []any{portedObject("model_name", "Tags", "fields", []any{"tag_name"}, "title", []any{"a"})},
			"At least one sheet must be visible"},
		{"dict-title-first", []any{portedObject("model_name", "Tags", "fields", []any{"tag_name"}, "title", portedObject("a", json.Number("1")))},
			"At least one sheet must be visible"},
		{"float-title-first", []any{portedObject("model_name", "Tags", "fields", []any{"tag_name"}, "title", json.Number("1.5"))},
			"At least one sheet must be visible"},
		{"bool-title-first", []any{portedObject("model_name", "Tags", "fields", []any{"tag_name"}, "title", false)},
			"At least one sheet must be visible"},
		{"labels-not-dict-first", []any{portedObject("model_name", "Tags", "fields", []any{"tag_name"}, "column_labels", []any{"x"})},
			"At least one sheet must be visible"},
		{"int-title-second", []any{portedObject("model_name", "Tags", "fields", []any{"tag_name"}, "title", "Etichette"),
			portedObject("model_name", "Tags", "fields", []any{"tag_name"}, "title", json.Number("5"))},
			"'int' object is not subscriptable"},
		{"list-title-second", []any{portedObject("model_name", "Tags", "fields", []any{"tag_name"}, "title", "Etichette"),
			portedObject("model_name", "Tags", "fields", []any{"tag_name"}, "title", []any{"a"})},
			"unhashable type: 'list'"},
		{"dict-title-second", []any{portedObject("model_name", "Tags", "fields", []any{"tag_name"}, "title", "Etichette"),
			portedObject("model_name", "Tags", "fields", []any{"tag_name"}, "title", portedObject("a", json.Number("1")))},
			"slice(None, 31, None)"},
		{"null-title-second", []any{portedObject("model_name", "Tags", "fields", []any{"tag_name"}, "title", "Etichette"),
			portedObject("model_name", "Tags", "fields", []any{"tag_name"}, "title", nil)},
			"'NoneType' object is not subscriptable"},
		{"labels-not-dict-second", []any{portedObject("model_name", "Tags", "fields", []any{"tag_name"}, "title", "Etichette"),
			portedObject("model_name", "Tags", "fields", []any{"tag_name"}, "column_labels", "abc")},
			"'str' object has no attribute 'get'"},
		{"label-list", []any{portedObject("model_name", "Tags", "fields", []any{"tag_name"},
			"column_labels", portedObject("tag_name", []any{"x"}))},
			"unhashable type: 'list'"},
		{"label-dict", []any{portedObject("model_name", "Tags", "fields", []any{"tag_name"},
			"column_labels", portedObject("tag_name", portedObject("a", json.Number("1"))))},
			"Passing a dict as an indexer is not supported. Use a list instead."},
	}
	for _, item := range titles {
		t.Run(item.name, func(t *testing.T) {
			result, raised := portedExecute(t, portedTools(sheetStore()), "export_multi_sheet",
				portedObject("sheets", item.specs))
			portedError(t, result, raised, item.want)
		})
	}
}

// ---------------------------------------------------------------------------
// get_attendance_matrix course arguments (item 13)
// ---------------------------------------------------------------------------

// attendanceStore answers the course resolution and one course matrix.
func attendanceStore(attendees string) *portedStore {
	return &portedStore{query: func(sql string, _ []any) [][]*string {
		text := func(value string) *string { return &value }
		switch {
		case strings.Contains(sql, `"application_course"`) && strings.Contains(sql, "COUNT"):
			return [][]*string{{text("0")}}
		case strings.Contains(sql, `"application_course"`):
			return [][]*string{{text("00000000-0000-0000-0000-000000017b1a"), text("Corso")}}
		case strings.Contains(sql, `"application_attendanceday"`):
			return [][]*string{{text("01/01/2026"), text(attendees)}}
		case strings.Contains(sql, `"application_attendanceregistry"`):
			return [][]*string{{text("1")}}
		default:
			return [][]*string{{text("abc"), text("Mario"), text("Rossi")}}
		}
	}}
}

func TestAttendanceCourseID(t *testing.T) {
	number := func(text string) json.Number { return json.Number(text) }
	notFound := []struct {
		name  string
		value any
		want  string
	}{
		{"int", number("5"), "Corso non trovato con ID: 5"},
		{"true", true, "Corso non trovato con ID: True"},
	}
	for _, item := range notFound {
		t.Run(item.name, func(t *testing.T) {
			// A course that is not found is a normal {'error': ...} result.
			result, _ := portedExecute(t, portedTools(&portedStore{}), "get_attendance_matrix",
				portedObject("course_id", item.value))
			if got := string(dto.PythonJSONDumps(result)); got != `{"error": `+string(dto.PythonJSONDumps(item.want))+`}` {
				t.Fatalf("result %s", got)
			}
		})
	}

	invalid := []struct {
		name  string
		value any
		want  string
	}{
		{"float", number("1.5"), `['"1.5" non è uno UUID valido.']`},
		{"list", []any{number("1")}, `['"[1]" non è uno UUID valido.']`},
		{"dict", portedObject("a", number("1")), `['"{\'a\': 1}" non è uno UUID valido.']`},
		{"nan", math.NaN(), `['"nan" non è uno UUID valido.']`},
	}
	for _, item := range invalid {
		t.Run(item.name, func(t *testing.T) {
			result, raised := portedExecute(t, portedTools(&portedStore{}), "get_attendance_matrix",
				portedObject("course_id", item.value))
			portedError(t, result, raised, item.want)
		})
	}

	t.Run("integer-uuid", func(t *testing.T) {
		fake := attendanceStore(`[]`)
		result, raised := portedExecute(t, portedTools(fake), "get_attendance_matrix",
			portedObject("course_id", number("97050")))
		if raised {
			t.Fatalf("raised: %s", string(dto.PythonJSONDumps(result)))
		}
		if len(fake.parameters) == 0 || len(fake.parameters[0]) == 0 {
			t.Fatalf("no parameters: %v", fake.parameters)
		}
		if fake.parameters[0][0] != "00000000-0000-0000-0000-000000017b1a" {
			t.Fatalf("pk %v", fake.parameters[0][0])
		}
		if course, _ := result.(domain.RequestObject).Lookup("course"); course != "Corso" {
			t.Fatalf("course %v", course)
		}
	})

	t.Run("course-name-int", func(t *testing.T) {
		result, _ := portedExecute(t, portedTools(attendanceStore(`[]`)), "get_attendance_matrix",
			portedObject("course_name", number("5")))
		if got := string(dto.PythonJSONDumps(result)); got != `{"error": "Nessun corso trovato con nome: 5"}` {
			t.Fatalf("result %s", got)
		}
	})
	t.Run("course-name-list", func(t *testing.T) {
		result, _ := portedExecute(t, portedTools(attendanceStore(`[]`)), "get_attendance_matrix",
			portedObject("course_name", []any{"x"}))
		if got := string(dto.PythonJSONDumps(result)); got != `{"error": "Nessun corso trovato con nome: [\u0027x\u0027]"}` &&
			got != `{"error": "Nessun corso trovato con nome: ['x']"}` {
			t.Fatalf("result %s", got)
		}
	})
}

// ---------------------------------------------------------------------------
// attendance days (items 15 and 16)
// ---------------------------------------------------------------------------

func attendanceIDsPreview(t *testing.T, store *portedStore, courseID string) domain.RequestObject {
	t.Helper()
	result, raised := portedExecute(t, portedTools(store), "get_attendance_matrix",
		portedObject("course_id", json.Number(courseID)))
	if raised {
		t.Fatalf("raised: %s", string(dto.PythonJSONDumps(result)))
	}
	return result.(domain.RequestObject)
}

func previewRow(t *testing.T, result domain.RequestObject) domain.RequestObject {
	t.Helper()
	preview, _ := result.Lookup("preview")
	rows := preview.([]any)
	if len(rows) != 1 {
		t.Fatalf("preview %v", preview)
	}
	return rows[0].(domain.RequestObject)
}

func TestAttendanceAttendees(t *testing.T) {
	present := func(t *testing.T, attendees string) int64 {
		t.Helper()
		row := previewRow(t, attendanceIDsPreview(t, attendanceStore(attendees), "97050"))
		count, _ := row.Lookup("present_count")
		value, _ := count.(json.Number).Int64()
		return value
	}
	for _, item := range []struct {
		attendees string
		want      int64
	}{
		{`["abc"]`, 1},
		{`[{"course_subscription_id": "abc"}]`, 1},
		{`[{"course_subscription_id": 5}]`, 0},
		{`[5]`, 0},
		{`[null]`, 0},
		{`[["abc"]]`, 0},
		{`{"abc": 1}`, 1},
		{`"abc"`, 0},
		{`[]`, 0},
	} {
		t.Run(item.attendees, func(t *testing.T) {
			if got := present(t, item.attendees); got != item.want {
				t.Fatalf("present_count %d, want %d", got, item.want)
			}
		})
	}
	uniterable := []struct{ attendees, want string }{
		{`5`, "'int' object is not iterable"},
		{`1.5`, "'float' object is not iterable"},
		{`true`, "'bool' object is not iterable"},
	}
	for _, item := range uniterable {
		t.Run("uniterable-"+item.attendees, func(t *testing.T) {
			result, raised := portedExecute(t, portedTools(attendanceStore(item.attendees)), "get_attendance_matrix",
				portedObject("course_id", json.Number("97050")))
			portedError(t, result, raised, item.want)
		})
	}
}

func TestAttendanceNameSort(t *testing.T) {
	store := &portedStore{query: func(sql string, _ []any) [][]*string {
		text := func(value string) *string { return &value }
		switch {
		case strings.Contains(sql, `"application_course"`):
			return [][]*string{{text("00000000-0000-0000-0000-000000017b1a"), text("Corso")}}
		case strings.Contains(sql, `"application_attendanceday"`):
			return [][]*string{{text("01/01/2026"), text(`[]`)}}
		case strings.Contains(sql, `"application_attendanceregistry"`):
			return [][]*string{{text("1")}}
		default:
			return [][]*string{{text("1"), text("Ivan"), text("")}, {text("2"), text("İlaria"), text("")}}
		}
	}}
	result := attendanceIDsPreview(t, store, "97050")
	preview, _ := result.Lookup("preview")
	rows := preview.([]any)
	if len(rows) != 2 {
		t.Fatalf("preview %v", preview)
	}
	first, _ := rows[0].(domain.RequestObject).Lookup("name")
	second, _ := rows[1].(domain.RequestObject).Lookup("name")
	// str.lower() of 'İlaria' is 'i' + combining dot: 'Ivan' sorts first.
	if first != "Ivan" || second != "İlaria" {
		t.Fatalf("order %v, %v", first, second)
	}
}

// ---------------------------------------------------------------------------
// get_attendance_matrix arguments written back (item 17)
// ---------------------------------------------------------------------------

func multiAttendanceStore() *portedStore {
	return &portedStore{query: func(sql string, _ []any) [][]*string {
		text := func(value string) *string { return &value }
		switch {
		case strings.Contains(sql, `"application_course"`):
			return [][]*string{
				{text("00000000-0000-0000-0000-000000017b1a"), text("Corso")},
				{text("00000000-0000-0000-0000-000000017b1b"), text("Altro")},
			}
		case strings.Contains(sql, `"application_attendanceday"`):
			return [][]*string{{text("01/01/2026"), text(`["abc"]`)}}
		case strings.Contains(sql, `"application_attendanceregistry"`):
			return [][]*string{{text("1")}}
		default:
			return [][]*string{{text("abc"), text("Mario"), text("Rossi")}}
		}
	}}
}

func TestAttendanceSaveInfo(t *testing.T) {
	cases := []struct {
		name        string
		arguments   domain.RequestObject
		multi       bool
		filename    string
		nameValue   any
		description string
		params      map[string]any
	}{
		{
			name:        "int-format",
			arguments:   portedObject("course_id", json.Number("97050"), "export", json.Number("1"), "format", json.Number("5")),
			filename:    "Presenze - Corso.xlsx",
			nameValue:   "Presenze inizio - oggi",
			description: "Registro presenze Corso - 1 iscritti, 1 lezioni",
			params: map[string]any{"export": json.Number("1"), "format": json.Number("5"),
				"course_id": json.Number("97050")},
		},
		{
			name:        "int-date-from",
			arguments:   portedObject("course_id", json.Number("97050"), "export", true, "date_from", json.Number("5")),
			filename:    "Presenze - Corso.xlsx",
			nameValue:   "Presenze 5 - oggi",
			description: "Registro presenze Corso (5 - oggi) - 1 iscritti, 1 lezioni",
			params: map[string]any{"export": true, "format": "xlsx", "course_id": json.Number("97050"),
				"date_from": json.Number("5")},
		},
		{
			name: "list-export",
			arguments: portedObject("course_id", json.Number("97050"), "export", []any{json.Number("1")},
				"multi_sheet", "yes"),
			filename:    "Presenze - Corso.xlsx",
			nameValue:   "Presenze inizio - oggi",
			description: "Registro presenze Corso - 1 iscritti, 1 lezioni",
			params: map[string]any{"export": []any{json.Number("1")}, "format": "xlsx",
				"course_id": json.Number("97050"), "multi_sheet": "yes"},
		},
		{
			name:        "multi-course",
			arguments:   portedObject("export", json.Number("1"), "multi_sheet", true),
			multi:       true,
			filename:    "Presenze - tutti i corsi.xlsx",
			nameValue:   "Presenze inizio - oggi",
			description: "Registro presenze Tutti i corsi - 2 iscritti, 2 lezioni, 2 corsi",
			params:      map[string]any{"export": json.Number("1"), "format": "xlsx", "multi_sheet": true},
		},
	}
	for _, item := range cases {
		t.Run(item.name, func(t *testing.T) {
			store := attendanceStore(`["abc"]`)
			if item.multi {
				store = multiAttendanceStore()
			}
			result, raised := portedExecute(t, portedTools(store), "get_attendance_matrix", item.arguments)
			if raised {
				t.Fatalf("raised: %s", string(dto.PythonJSONDumps(result)))
			}
			object := result.(domain.RequestObject)
			if filename, _ := object.Lookup("filename"); filename != item.filename {
				t.Fatalf("filename %v", filename)
			}
			if _, present := object.Lookup("data_base64"); !present {
				t.Fatalf("no file: %s", string(dto.PythonJSONDumps(object)))
			}
			info, _ := object.Lookup("save_info")
			saveInfo := info.(domain.RequestObject)
			if name, _ := saveInfo.Lookup("name"); name != item.nameValue {
				t.Fatalf("name %v", name)
			}
			if description, _ := saveInfo.Lookup("description"); description != item.description {
				t.Fatalf("description %v", description)
			}
			params, _ := saveInfo.Lookup("params")
			paramsObject := params.(domain.RequestObject)
			if got := string(dto.PythonJSONDumps(paramsObject)); got != string(dto.PythonJSONDumps(portedMap(item.params))) {
				t.Fatalf("params %s", got)
			}
		})
	}
}

// portedMap is a RequestObject in the argument order of the save_info params.
func portedMap(values map[string]any) domain.RequestObject {
	order := []string{"export", "format", "course_id", "course_name", "date_from", "date_to", "multi_sheet"}
	object := domain.RequestObject{}
	for _, name := range order {
		if value, present := values[name]; present {
			object = append(object, domain.RequestField{Name: name, Value: value})
		}
	}
	return object
}

// ---------------------------------------------------------------------------
// export_data PDF texts (item 18)
// ---------------------------------------------------------------------------

func TestExportPDFTexts(t *testing.T) {
	cases := []struct {
		name  string
		extra domain.RequestObject
		want  string
	}{
		{"int-label", portedObject("column_labels", portedObject("tag_name", json.Number("5"))),
			"'int' object has no attribute 'split'"},
		{"list-label", portedObject("column_labels", portedObject("tag_name", []any{json.Number("1")})),
			"'list' object has no attribute 'split'"},
		{"dict-label", portedObject("column_labels", portedObject("tag_name", portedObject("a", json.Number("1")))),
			"'dict' object has no attribute 'split'"},
		{"null-label", portedObject("column_labels", portedObject("tag_name", nil)),
			"'NoneType' object has no attribute 'split'"},
		{"int-before", portedObject("text_before", json.Number("5")),
			"'int' object has no attribute 'split'"},
		{"list-before", portedObject("text_before", []any{"x"}),
			"'list' object has no attribute 'split'"},
		{"int-after", portedObject("text_after", json.Number("5")),
			"'int' object has no attribute 'split'"},
		{"int-header", portedObject("header_text", json.Number("5")),
			"'int' object has no attribute 'decode'"},
		{"true-header", portedObject("header_text", true),
			"'bool' object has no attribute 'decode'"},
		{"list-header", portedObject("header_text", []any{"x"}),
			"'list' object has no attribute 'decode'"},
	}
	for _, item := range cases {
		t.Run(item.name, func(t *testing.T) {
			arguments := portedObject("format", "pdf", "model_name", "Tags", "fields", []any{"tag_name"})
			arguments = append(arguments, item.extra...)
			result, raised := portedExecute(t, portedTools(sheetStore()), "export_data", arguments)
			portedError(t, result, raised, item.want)
		})
	}

	t.Run("label-before-before", func(t *testing.T) {
		arguments := portedObject("format", "pdf", "model_name", "Tags", "fields", []any{"tag_name"},
			"column_labels", portedObject("tag_name", json.Number("5")), "text_before", json.Number("5"))
		result, raised := portedExecute(t, portedTools(sheetStore()), "export_data", arguments)
		portedError(t, result, raised, "'int' object has no attribute 'split'")
	})
	t.Run("before-before-after", func(t *testing.T) {
		arguments := portedObject("format", "pdf", "model_name", "Tags", "fields", []any{"tag_name"},
			"text_after", json.Number("5"), "text_before", json.Number("5"))
		result, raised := portedExecute(t, portedTools(sheetStore()), "export_data", arguments)
		portedError(t, result, raised, "'int' object has no attribute 'split'")
	})
}

// ---------------------------------------------------------------------------
// saved-report run: the three file tools answer a download, never a refusal
// ---------------------------------------------------------------------------

// runStore answers a saved-report run: one stored report and the file tools'
// statements.
type runStore struct {
	report domain.SavedReport
	runErr error
}

func (runStore) ReportIntegration(context.Context) (bool, *string, error) { return false, nil, nil }

func (s runStore) RunReportStatements(_ context.Context, _ int64, statements []domain.ReportStatement) ([][][]*string, error) {
	if s.runErr != nil {
		return nil, s.runErr
	}
	results := make([][][]*string, 0, len(statements))
	for _, statement := range statements {
		text := func(value string) *string { return &value }
		switch {
		case strings.Contains(statement.SQL, "COUNT"):
			results = append(results, [][]*string{{text("2")}})
		case strings.Contains(statement.SQL, `"application_attendanceday"`):
			results = append(results, [][]*string{{text("01/01/2026"), text(`["abc"]`)}})
		case strings.Contains(statement.SQL, `"application_attendanceregistry"`):
			results = append(results, [][]*string{{text("1")}})
		case strings.Contains(statement.SQL, `"application_course"`):
			results = append(results, [][]*string{{text("00000000-0000-0000-0000-000000017b1a"), text("Corso")}})
		case strings.Contains(statement.SQL, `"application_coursesubscription"`):
			results = append(results, [][]*string{{text("abc"), text("Mario"), text("Rossi")}})
		default:
			results = append(results, [][]*string{{text("WSAgent Alfa")}, {text("WSAgent Beta")}})
		}
	}
	return results, nil
}

func (runStore) ActiveAssociationForUser(context.Context, string) (string, error) {
	return portedAssociation, nil
}
func (runStore) SavedReports(context.Context, string, string) ([]domain.SavedReport, error) {
	return nil, nil
}
func (s runStore) SavedReport(context.Context, string, string, string) (domain.SavedReport, error) {
	return s.report, nil
}
func (runStore) InsertSavedReport(context.Context, string, string, string, domain.SavedReportWrite) error {
	return nil
}
func (runStore) UpdateSavedReport(context.Context, string, domain.SavedReportChange) error {
	return nil
}
func (runStore) DeleteSavedReport(context.Context, string) error { return nil }

func runReport(t *testing.T, tool, params string) (*service.ReportDownload, error) {
	t.Helper()
	return runReportStore(t, runStore{report: domain.SavedReport{ID: "a1000000-0000-4000-8000-000000000010", Name: "Report",
		ToolName: tool, Params: params, UIConfig: "{}"}})
}

func runReportStore(t *testing.T, store runStore) (*service.ReportDownload, error) {
	t.Helper()
	reports := service.NewSavedReports(store, func() time.Time {
		return time.Date(2026, 11, 16, 9, 30, 0, 0, time.UTC)
	})
	identity := domain.ResolvedIdentity{ScopeUser: &domain.Account{ID: portedUser}}
	result, err := reports.RunGroup(context.Background(), identity, "a1000000-0000-4000-8000-000000000010", nil, "")
	if err != nil {
		return nil, err
	}
	download, ok := result.(*service.ReportDownload)
	if !ok {
		t.Fatalf("result %T", result)
	}
	return download, nil
}

func TestSavedReportRunFiles(t *testing.T) {
	cases := []struct {
		name        string
		tool        string
		params      string
		contentType string
		disposition string
	}{
		{
			name:        "csv",
			tool:        "export_data",
			params:      `{"model_name": "Tags", "fields": ["tag_name"], "format": "csv", "title": "a/b"}`,
			contentType: "text/csv; charset=utf-8",
			disposition: `attachment; filename="a-b.csv"`,
		},
		{
			name:        "multi-sheet",
			tool:        "export_multi_sheet",
			params:      `{"sheets": [{"model_name": "Tags", "fields": ["tag_name"]}]}`,
			contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
			disposition: `attachment; filename="Export.xlsx"`,
		},
		{
			name:        "attendance",
			tool:        "get_attendance_matrix",
			params:      `{"course_id": 97050, "export": true, "format": 5}`,
			contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
			disposition: `attachment; filename="Presenze - Corso.xlsx"`,
		},
	}
	for _, item := range cases {
		t.Run(item.name, func(t *testing.T) {
			download, err := runReport(t, item.tool, item.params)
			if err != nil {
				t.Fatalf("run: %v", err)
			}
			if download.ContentType != item.contentType || string(download.Disposition) != item.disposition ||
				len(download.Bytes) == 0 {
				t.Fatalf("download %s %q (%d bytes)", download.ContentType, download.Disposition, len(download.Bytes))
			}
		})
	}

	t.Run("exception-is-the-json-500", func(t *testing.T) {
		_, err := runReport(t, "export_data", `{"model_name": "Tags", "fields": ["tag_name"], "format": "csv", "title": 5}`)
		if !errors.Is(err, dto.ErrReportFailed) || errors.Is(err, service.ErrBusinessMCPNotPorted) {
			t.Fatalf("expected dto.ErrReportFailed, got %v", err)
		}
	})

	t.Run("database-error-is-the-json-500", func(t *testing.T) {
		store := runStore{report: domain.SavedReport{ID: "a1000000-0000-4000-8000-000000000010", Name: "Report",
			ToolName: "export_data", Params: `{"model_name": "Tags", "fields": ["tag_name"], "format": "csv"}`,
			UIConfig: "{}"}, runErr: &domain.ReportQueryError{Err: errors.New("connection lost")}}
		_, err := runReportStore(t, store)
		if !errors.Is(err, dto.ErrReportFailed) || errors.Is(err, service.ErrBusinessMCPNotPorted) {
			t.Fatalf("expected dto.ErrReportFailed, got %v", err)
		}
	})
}

// ---------------------------------------------------------------------------
// export_data values of a JSON column (item 19)
// ---------------------------------------------------------------------------

// jsonColumnStore answers one BalanceSheet row whose JSON data holds value.
func jsonColumnStore(value string) *portedStore {
	return &portedStore{query: func(sql string, _ []any) [][]*string {
		text := func(value string) *string { return &value }
		if strings.Contains(sql, "COUNT") {
			return [][]*string{{text("1")}}
		}
		return [][]*string{{text(value)}}
	}}
}

func TestExportDataJSONColumnValues(t *testing.T) {
	big := `{"n": 123456789012345678901234567890}`
	xlsx, raised := portedExecute(t, portedTools(jsonColumnStore(big)), "export_data",
		portedObject("format", "xlsx", "model_name", "BalanceSheet", "fields", []any{"data"}))
	if raised {
		t.Fatalf("raised: %s", string(dto.PythonJSONDumps(xlsx)))
	}
	if file, _ := xlsx.(domain.RequestObject).Lookup("data_base64"); file == "" || file == nil {
		t.Fatalf("no file: %s", string(dto.PythonJSONDumps(xlsx)))
	}
	csv, raised := portedExecute(t, portedTools(jsonColumnStore(big)), "export_data",
		portedObject("format", "csv", "model_name", "BalanceSheet", "fields", []any{"data"}))
	if raised {
		t.Fatalf("raised: %s", string(dto.PythonJSONDumps(csv)))
	}
	encoded, _ := csv.(domain.RequestObject).Lookup("data_base64")
	file, err := base64.StdEncoding.DecodeString(encoded.(string))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(file), `{'n': 123456789012345678901234567890}`) {
		t.Fatalf("csv %q", string(file))
	}
}

// ---------------------------------------------------------------------------
// export_data argument types (item 10)
// ---------------------------------------------------------------------------

func TestExportDataArgumentTypes(t *testing.T) {
	cases := []struct {
		name  string
		extra domain.RequestObject
		want  string
	}{
		{"float-title", portedObject("title", math.NaN()), "expected string or bytes-like object, got 'float'"},
		{"inf-title", portedObject("title", math.Inf(1)), "expected string or bytes-like object, got 'float'"},
		{"string-labels", portedObject("column_labels", "abc"), "'str' object has no attribute 'get'"},
		{"list-labels", portedObject("column_labels", []any{"x"}), "'list' object has no attribute 'get'"},
		{"int-labels", portedObject("column_labels", json.Number("5")), "'int' object has no attribute 'get'"},
		{"float-labels", portedObject("column_labels", json.Number("1.5")), "'float' object has no attribute 'get'"},
	}
	for _, item := range cases {
		t.Run(item.name, func(t *testing.T) {
			arguments := portedObject("format", "csv", "model_name", "Tags", "fields", []any{"tag_name"})
			arguments = append(arguments, item.extra...)
			result, raised := portedExecute(t, portedTools(sheetStore()), "export_data", arguments)
			portedError(t, result, raised, item.want)
		})
	}
}

func TestExportDataCSVNUL(t *testing.T) {
	arguments := portedObject("format", "csv", "model_name", "Tags", "fields", []any{"tag_name"},
		"column_labels", portedObject("tag_name", "a\x00b"))
	result, raised := portedExecute(t, portedTools(sheetStore()), "export_data", arguments)
	if raised {
		t.Fatalf("raised: %s", string(dto.PythonJSONDumps(result)))
	}
	encoded, _ := result.(domain.RequestObject).Lookup("data_base64")
	file, err := base64.StdEncoding.DecodeString(encoded.(string))
	if err != nil {
		t.Fatal(err)
	}
	// csv.writer writes the NUL as it is, without quoting the cell.
	if !strings.Contains(string(file), "a\x00b\r\n") {
		t.Fatalf("csv %q", string(file))
	}
}

// ---------------------------------------------------------------------------
// agent tool result (item 4)
// ---------------------------------------------------------------------------

func TestAgentAnswersTheGenericToolError(t *testing.T) {
	store := &portedStore{runErr: &domain.ReportQueryError{Err: errors.New("connection lost")}}
	agent := &service.AgentTools{BusinessTools: portedTools(store)}
	result, raised, err := agent.Business(context.Background(), "query_data", portedObject("model_name", "Tags"))
	if err != nil || !raised {
		t.Fatalf("result %v raised %v err %v", result, raised, err)
	}
	if got := string(dto.PythonJSONDumps(result)); got != `{"error": "Errore durante l'esecuzione dello strumento."}` {
		t.Fatalf("result %s", got)
	}
}
