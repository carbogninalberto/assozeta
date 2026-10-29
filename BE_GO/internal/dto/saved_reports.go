package dto

import (
	"encoding/json"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// PythonIsoformat is datetime.isoformat() of an aware UTC value (+00:00 suffix).
func PythonIsoformat(value time.Time) string {
	value = value.UTC()
	if value.Nanosecond()/1000 == 0 {
		return value.Format("2006-01-02T15:04:05+00:00")
	}
	return value.Format("2006-01-02T15:04:05.000000+00:00")
}

type SavedReportSummary struct {
	ID          string `json:"saved_report_id"`
	Name        string `json:"name"`
	Description string `json:"description"`
	ToolName    string `json:"tool_name"`
	CreatedAt   string `json:"created_at"`
	UpdatedAt   string `json:"updated_at"`
}

type SavedReportDetail struct {
	SavedReportSummary
	Params   json.RawMessage `json:"params"`
	UIConfig json.RawMessage `json:"ui_config"`
}

func SavedReportSummaryFrom(row domain.SavedReport) SavedReportSummary {
	return SavedReportSummary{ID: row.ID, Name: row.Name, Description: row.Description, ToolName: row.ToolName,
		CreatedAt: PythonIsoformat(row.CreatedAt), UpdatedAt: PythonIsoformat(row.UpdatedAt)}
}

// SavedReportTools are TOOL_FUNCTIONS' keys in BE/application/views/report_views.py.
var SavedReportTools = map[string]bool{"get_schema": true, "query_data": true, "count_data": true, "get_field_values": true,
	"export_data": true, "aggregate_data": true, "get_attendance_matrix": true, "export_multi_sheet": true}

// PythonStripString is str.strip() of a value that must be a string.
func PythonStripString(value any) (string, bool) {
	text, ok := value.(string)
	if !ok {
		return "", false
	}
	return pythonStrip(text), true
}
