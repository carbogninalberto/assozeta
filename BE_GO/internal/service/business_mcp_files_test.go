package service_test

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
)

// fileStore answers the QueryBuilder statements of one Tags column with two
// rows, so the export tools build a real workbook or document.
type fileStore struct{}

func (fileStore) SavedReports(context.Context, string, string) ([]domain.SavedReport, error) {
	return nil, nil
}
func (fileStore) InsertSavedReport(context.Context, string, string, string, domain.SavedReportWrite) error {
	return nil
}
func (fileStore) ActiveUserExists(context.Context, string) (bool, error)  { return true, nil }
func (fileStore) AssociationExists(context.Context, string) (bool, error) { return true, nil }
func (fileStore) ReportIntegration(context.Context) (bool, *string, error) {
	return false, nil, nil
}
func (fileStore) RunReportStatements(_ context.Context, _ int64, statements []domain.ReportStatement) ([][][]*string, error) {
	rows := [][][]*string{}
	for _, statement := range statements {
		text := func(value string) *string { return &value }
		if strings.Contains(statement.SQL, "COUNT") {
			rows = append(rows, [][]*string{{text("2")}})
			continue
		}
		rows = append(rows, [][]*string{{text("WSAgent Alfa")}, {text("WSAgent Beta")}})
	}
	return rows, nil
}

// exportCall runs export_data the way the agent's tool runner does (raised
// marks a result that stands for an exception).
func exportCall(t *testing.T, arguments string) (result any, raised bool, err error) {
	t.Helper()
	parsed, parseErr := dto.ParseReferenceJSON([]byte(arguments))
	if parseErr != nil {
		t.Fatal(parseErr)
	}
	tools := service.NewBusinessMCPTools(fileStore{}, "a1000000-0000-4000-8000-000000000001", time.Now)
	raised = false
	result, err = tools.Execute(service.WithMCPRaised(context.Background(), &raised), "export_data",
		parsed.(domain.RequestObject))
	return result, raised, err
}

func TestExportDataFileErrors(t *testing.T) {
	cases := []struct {
		name, format string
		extra        map[string]any
		want         string
	}{
		{"xlsx-title", "xlsx", map[string]any{"title": "a/b"}, "Invalid character / found in sheet title"},
		{"xlsx-label-list", "xlsx", map[string]any{"column_labels": map[string]any{"tag_name": []any{"x"}}},
			"unhashable type: 'list'"},
		{"xlsx-label-dict", "xlsx", map[string]any{"column_labels": map[string]any{"tag_name": map[string]any{"a": 1}}},
			"Passing a dict as an indexer is not supported. Use a list instead."},
		{"pdf-unclosed", "pdf", map[string]any{"column_labels": map[string]any{"tag_name": "A<B"}},
			"paraparser: syntax error: parse ended with 1 unclosed tags\n para"},
		{"pdf-stray-close", "pdf", map[string]any{"column_labels": map[string]any{"tag_name": "a</i>"}},
			"\nparagraph text '<para>a</i></para>' caused exception Parse error: saw </i> instead of expected </para>"},
	}
	for _, item := range cases {
		t.Run(item.name, func(t *testing.T) {
			arguments := map[string]any{"format": item.format, "model_name": "Tags", "fields": []any{"tag_name"},
				"filters": map[string]any{"tag_name__startswith": "WSAgent"}, "order_by": []any{"tag_name"}}
			for key, value := range item.extra {
				arguments[key] = value
			}
			encoded, err := json.Marshal(arguments)
			if err != nil {
				t.Fatal(err)
			}
			result, raised, err := exportCall(t, string(encoded))
			if err != nil {
				t.Fatalf("call: %v", err)
			}
			if !raised {
				t.Fatalf("the exception is not marked raised: %v", result)
			}
			if got := string(dto.PythonJSONDumps(result)); got != `{"error": `+string(dto.PythonJSONDumps(item.want))+`}` {
				t.Fatalf("result %s", got)
			}
		})
	}
}

// The XLSX and PDF results themselves: the file, its name and the save_info the
// consumer caches and frames.
func TestExportDataFiles(t *testing.T) {
	const base = `{"format": "%s", "model_name": "Tags", "fields": ["tag_name"], "filters": {"tag_name__startswith": "WSAgent"}, "order_by": ["tag_name"], "title": "Etichette"}`
	types := map[string]string{"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
		"pdf": "application/pdf"}
	for format, contentType := range types {
		t.Run(format, func(t *testing.T) {
			result, raised, err := exportCall(t, strings.Replace(base, "%s", format, 1))
			if err != nil || raised {
				t.Fatalf("call: %v raised=%v", err, raised)
			}
			object := result.(domain.RequestObject)
			value := func(name string) any {
				item, _ := object.Lookup(name)
				return item
			}
			if value("filename") != "Etichette."+format || value("content_type") != contentType {
				t.Fatalf("filename %v content_type %v", value("filename"), value("content_type"))
			}
			encoded, _ := value("data_base64").(string)
			file, decodeErr := base64.StdEncoding.DecodeString(encoded)
			if decodeErr != nil || len(file) == 0 {
				t.Fatalf("file: %v (%d bytes)", decodeErr, len(file))
			}
			info, _ := value("save_info").(domain.RequestObject)
			name, _ := info.Lookup("name")
			if !strings.HasPrefix(name.(string), "Etichette - ") {
				t.Fatalf("save_info name %v", name)
			}
		})
	}
}

// A title that is not a str raises in sanitize_text; the MCP command answers
// its text, the agent X11's generic result (ws-agent.md).
func TestExportDataTitleNotString(t *testing.T) {
	arguments := `{"format": "xlsx", "model_name": "Tags", "fields": ["tag_name"], "title": 5}`
	result, raised, err := exportCall(t, arguments)
	if err != nil || !raised {
		t.Fatalf("expected the raised result, got %v raised=%v", err, raised)
	}
	if got := string(dto.PythonJSONDumps(result)); got != `{"error": "expected string or bytes-like object, got 'int'"}` {
		t.Fatalf("result %s", got)
	}
}
