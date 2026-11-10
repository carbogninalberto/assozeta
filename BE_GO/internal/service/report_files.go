package service

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"math"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pdfexport"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pdfrender"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/xlsxexport"
)

// The files of export_data, export_multi_sheet and get_attendance_matrix
// (BE/application/mcp_server/exporters and attendance.py) for
// saved-reports/<pk>/run: the tool functions of business_mcp_query.go build
// the result, pdfexport and xlsxexport the documents.

const reportXLSXType = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

type reportRunKey struct{}

// ReportDownload is a tool result with data_base64 as the run view answers
// it: the file, its content type and the Content-Disposition header bytes.
type ReportDownload struct {
	Bytes       []byte
	ContentType string
	Disposition string
}

// reportRunStore lends the query store to the tool functions a run uses.
type reportRunStore struct{ BusinessMCPQueryStore }

func (reportRunStore) SavedReports(context.Context, string, string) ([]domain.SavedReport, error) {
	return nil, ErrBusinessMCPNotPorted
}
func (reportRunStore) InsertSavedReport(context.Context, string, string, string, domain.SavedReportWrite) error {
	return ErrBusinessMCPNotPorted
}
func (reportRunStore) ActiveUserExists(context.Context, string) (bool, error) {
	return false, ErrBusinessMCPNotPorted
}
func (reportRunStore) AssociationExists(context.Context, string) (bool, error) {
	return false, ErrBusinessMCPNotPorted
}

// runFileTool runs one of the three file tools as SavedReportViewSet.run
// does: an exception is the JSON 500, a result with 'error' the 400 with the
// whole result, a result with data_base64 the download.
func (s *SavedReports) runFileTool(ctx context.Context, tool string, params domain.RequestObject, association string, group *string) (any, error) {
	if _, missing := BusinessBindingError(tool, params); missing {
		return nil, dto.ErrReportFailed
	}
	raised := false
	ctx = context.WithValue(WithMCPRaised(ctx, &raised), reportRunKey{}, true)
	tools := &BusinessMCPTools{store: reportRunStore{s.store}, association: association, now: s.now, files: true, group: group}
	result, err := tools.Execute(ctx, tool, params)
	if err != nil {
		return nil, err
	}
	if raised {
		return nil, dto.ErrReportFailed
	}
	object, ok := result.(domain.RequestObject)
	if !ok {
		return nil, ErrBusinessMCPNotPorted
	}
	if message, failed := object.Lookup("error"); failed {
		text, _ := message.(string)
		return nil, &dto.ReportToolError{Message: text, Result: object}
	}
	encoded, file := object.Lookup("data_base64")
	if !file {
		return object, nil
	}
	data, err := base64.StdEncoding.DecodeString(encoded.(string))
	if err != nil {
		return nil, err
	}
	contentType, _ := object.Lookup("content_type")
	filename, _ := object.Lookup("filename")
	disposition := `attachment; filename="` + filename.(string) + `"`
	if !pdfrender.HeaderSafe(disposition) {
		return nil, ErrSavedReportRaised // BadHeaderError, outside the view's try
	}
	return &ReportDownload{Bytes: data, ContentType: contentType.(string), Disposition: pdfrender.HeaderBytes(disposition)}, nil
}

// courseGroup is the X-Group-Id filter of Course.objects during a request.
func (t *BusinessMCPTools) courseGroup() string {
	if t.group == nil {
		return ""
	}
	if _, err := paymentInfoParseUUID(*t.group); err != nil {
		return ""
	}
	return ` AND "application_course"."group_id" = '` + *t.group + `'::uuid`
}

// exportColumns are format_export_data's kept keys and their header labels.
func mcpExportColumns(data []any, labels domain.RequestObject) ([]string, []string, error) {
	var columns, headers []string
	for _, field := range data[0].(domain.RequestObject) {
		if mcpExportBlacklist[field.Name] {
			continue
		}
		header := field.Name
		if label, present := labels.Lookup(field.Name); present {
			text, ok := label.(string)
			if !ok {
				return nil, nil, ErrBusinessMCPNotPorted // a label that is not a str
			}
			header = text
		}
		columns, headers = append(columns, field.Name), append(headers, header)
	}
	return columns, headers, nil
}

func mcpExportXLSXValue(value any) (xlsxexport.Value, error) {
	formatted, err := mcpFormatExportValue(value)
	if err != nil {
		return xlsxexport.Value{}, err
	}
	switch typed := formatted.(type) {
	case string:
		return xlsxexport.StrValue(typed), nil
	case json.Number:
		text := string(typed)
		if strings.ContainsAny(text, ".eEinIN") {
			number, err := strconv.ParseFloat(text, 64)
			if err != nil || math.IsInf(number, 0) || math.IsNaN(number) {
				return xlsxexport.Value{}, ErrBusinessMCPNotPorted
			}
			return xlsxexport.FloatValue(number), nil
		}
		number, err := strconv.ParseInt(text, 10, 64)
		if err != nil {
			return xlsxexport.Value{}, ErrBusinessMCPNotPorted
		}
		return xlsxexport.IntValue(number), nil
	case domain.RequestObject, []any:
		return xlsxexport.ObjectValue(dto.PythonStr(typed)), nil
	}
	return xlsxexport.Value{}, ErrBusinessMCPNotPorted
}

// reportSheet is one DataFrame of XLSXExporter: the formatted values by column.
func mcpReportSheet(title string, data []any, labels domain.RequestObject) (xlsxexport.ReportSheet, error) {
	columns, _, _ := mcpExportColumns(data, nil)
	sheet := xlsxexport.ReportSheet{Title: title, Data: make([][]xlsxexport.Value, len(columns))}
	// df.rename(columns=...): a label that is not a str names the column as
	// it is; a list or dict raises.
	for _, column := range columns {
		label := xlsxexport.StrValue(column)
		if value, present := labels.Lookup(column); present {
			switch typed := value.(type) {
			case string:
				label = xlsxexport.StrValue(typed)
			case nil:
				label = xlsxexport.NoneValue()
			case bool:
				label = xlsxexport.BoolValue(typed)
			case json.Number:
				var err error
				if label, err = mcpExportXLSXValue(typed); err != nil {
					return xlsxexport.ReportSheet{}, err
				}
			default:
				return xlsxexport.ReportSheet{}, xlsxexport.ErrDjango
			}
		}
		sheet.Labels = append(sheet.Labels, label)
	}
	sheet.Columns = xlsxexport.LabelTexts(sheet.Labels)
	for _, item := range data {
		row := item.(domain.RequestObject)
		for i, column := range columns {
			value, _ := row.Lookup(column)
			typed, err := mcpExportXLSXValue(value)
			if err != nil {
				return xlsxexport.ReportSheet{}, err
			}
			sheet.Data[i] = append(sheet.Data[i], typed)
		}
	}
	return sheet, nil
}

// builtFile maps a document builder's outcome: an exception of the Django
// exporter marks the call raised (nil file), a refused input stays refused.
func mcpBuiltFile(ctx context.Context, file []byte, err error) ([]byte, error) {
	switch {
	case errors.Is(err, xlsxexport.ErrDjango), errors.Is(err, pdfexport.ErrLayout):
		markMCPRaised(ctx)
		return nil, nil
	case errors.Is(err, xlsxexport.ErrNotPorted), errors.Is(err, pdfexport.ErrNotPorted):
		return nil, ErrBusinessMCPNotPorted
	}
	return file, err
}

func (t *BusinessMCPTools) exportXLSX(ctx context.Context, data []any, labels domain.RequestObject, title string) ([]byte, error) {
	sheet, err := mcpReportSheet(title, data, labels)
	if err != nil {
		return mcpBuiltFile(ctx, nil, err)
	}
	file, err := xlsxexport.ReportWorkbook(sheet, t.now())
	return mcpBuiltFile(ctx, file, err)
}

// mcpOptionalText is a str argument with a ” default: falsy values count as
// ”; ok is false for other values that are not a str.
func mcpOptionalText(arguments domain.RequestObject, name string) (string, bool) {
	value, _ := arguments.Lookup(name)
	if !mcpTruthy(value) {
		return "", true
	}
	text, ok := value.(string)
	return text, ok
}

func (t *BusinessMCPTools) exportPDF(ctx context.Context, data []any, labels domain.RequestObject, title string, arguments domain.RequestObject) ([]byte, error) {
	raised := func() ([]byte, error) {
		markMCPRaised(ctx)
		return nil, nil
	}
	// PDFExporter raises on: a label that is not a str (Paragraph), a table
	// without columns, a header or body text that is not a str (stringWidth,
	// Paragraph). The footer is written with '%s'.
	columns, _, _ := mcpExportColumns(data, nil)
	if len(columns) == 0 {
		return raised()
	}
	for _, column := range columns {
		if label, present := labels.Lookup(column); present {
			if _, ok := label.(string); !ok {
				return raised()
			}
		}
	}
	header, headerOK := mcpOptionalText(arguments, "header_text")
	before, beforeOK := mcpOptionalText(arguments, "text_before")
	after, afterOK := mcpOptionalText(arguments, "text_after")
	if !headerOK || !beforeOK || !afterOK {
		return raised()
	}
	columns, headers, err := mcpExportColumns(data, labels)
	if err != nil {
		return nil, err
	}
	table := pdfexport.ReportTable{Headers: headers, Title: title}
	for _, item := range data {
		row := item.(domain.RequestObject)
		line := make([]string, len(columns))
		for i, column := range columns {
			value, _ := row.Lookup(column)
			formatted, err := mcpFormatExportValue(value)
			if err != nil {
				return nil, err
			}
			line[i] = dto.PythonStr(formatted)
		}
		table.Rows = append(table.Rows, line)
	}
	if header != "" {
		table.Title = header
	}
	if footer, _ := arguments.Lookup("footer_text"); mcpTruthy(footer) {
		table.Footer = dto.PythonStr(footer)
	}
	table.TextBefore, table.TextAfter = before, after
	file, err := table.Build(t.now())
	return mcpBuiltFile(ctx, file, err)
}

type reportSheetData struct {
	data  []any
	spec  domain.RequestObject
	model any
}

func mcpFileResult(filename, contentType string, file []byte) domain.RequestObject {
	return mcpObj("type", "export_ready", "filename", filename, "content_type", contentType,
		"data_base64", base64.StdEncoding.EncodeToString(file))
}

// exportSheets is the end of tool_export_multi_sheet for a run: the file
// name, the workbook and the result's file keys.
func (t *BusinessMCPTools) exportSheets(ctx context.Context, prepared []reportSheetData, arguments domain.RequestObject) (any, error) {
	raise := func() (any, error) {
		markMCPRaised(ctx)
		return mcpErrorResult(""), nil
	}
	name := "Export"
	switch value := mcpLookupOr(arguments, "filename", "Export").(type) {
	case string:
		var err error
		if name, err = SanitizeText(value, "-"); err != nil {
			return nil, err
		}
	default:
		if mcpTruthy(value) {
			return raise() // re.sub on a value that is not a str
		}
		name = dto.PythonStr(value)
	}
	var sheets []xlsxexport.ReportSheet
	for _, item := range prepared {
		title := item.model
		if value, present := item.spec.Lookup("title"); present {
			title = value
		}
		text, ok := title.(string)
		if !ok {
			return raise() // raw_title[:31] or the name lookup fails
		}
		labels := domain.RequestObject{}
		if value, _ := item.spec.Lookup("column_labels"); mcpTruthy(value) {
			object, ok := value.(domain.RequestObject)
			if !ok {
				return raise() // .get on a value that is not a dict
			}
			labels = dto.PythonDict(object)
		}
		sheet, err := mcpReportSheet(text, item.data, labels)
		if errors.Is(err, xlsxexport.ErrDjango) {
			return raise()
		}
		if err != nil {
			return nil, err
		}
		sheets = append(sheets, sheet)
	}
	file, err := xlsxexport.ReportSheetsWorkbook(sheets, t.now())
	if file, err = mcpBuiltFile(ctx, file, err); err != nil || file == nil {
		return mcpErrorResult(""), err
	}
	return mcpFileResult(name+".xlsx", reportXLSXType, file), nil
}

// attendanceFile is the export branch of tool_get_attendance_matrix for a
// run: the PDF, or the workbook in one of its three layouts.
func (t *BusinessMCPTools) attendanceFile(ctx context.Context, matrices []*attendanceMatrix, multi bool, arguments domain.RequestObject) (any, error) {
	if !t.files {
		return nil, ErrBusinessMCPNotPorted // the files are built for a saved-report run only
	}
	raised := func() (any, error) {
		markMCPRaised(ctx)
		return mcpErrorResult(""), nil
	}
	pdf := false
	if format, ok := mcpLookupOr(arguments, "format", "xlsx").(string); ok && format == "pdf" {
		pdf = true
	}
	name := "Presenze - tutti i corsi"
	if !multi {
		// f'Presenze - {course}': a course without a title is written None.
		name = "Presenze - " + dto.PythonStr(mcpOptional(matrices[0].course))
		if matrices[0].course == nil && !pdf {
			return raised() // the sheet name is a slice of the title
		}
	}
	var document pdfexport.Attendance
	var courses []xlsxexport.AttendanceCourse
	for _, matrix := range matrices {
		title := ""
		if matrix.course != nil {
			title = *matrix.course
		} else if pdf && len(matrices) > 1 {
			return raised() // Paragraph(None) above the course's table
		}
		dates := make([]string, len(matrix.dates))
		for i, date := range matrix.dates {
			dates[i] = date.(string)
		}
		// Without a title: no heading on a one-course document, the sheet
		// named Foglio, an empty Corso cell.
		page := pdfexport.AttendanceCourse{Course: title, Dates: dates}
		sheet := xlsxexport.AttendanceCourse{Course: title, Untitled: matrix.course == nil, Dates: dates}
		for _, row := range matrix.rows {
			page.Rows = append(page.Rows, pdfexport.AttendanceRow{Name: row.name, Attendance: row.marks, Present: row.present})
			sheet.Rows = append(sheet.Rows, xlsxexport.AttendanceRow{Name: row.name, Attendance: row.marks, Present: int64(row.present)})
		}
		document.Courses, courses = append(document.Courses, page), append(courses, sheet)
	}
	var file []byte
	var err error
	contentType := reportXLSXType
	if pdf {
		contentType, name = "application/pdf", name+".pdf"
		file, err = document.Build(t.now())
	} else {
		mode := "single"
		if multi {
			mode = "flat"
			if mcpTruthy(mcpLookupOr(arguments, "multi_sheet", false)) {
				mode = "sheets"
			}
		}
		name += ".xlsx"
		file, err = xlsxexport.AttendanceWorkbook(mode, courses, t.now())
	}
	if file, err = mcpBuiltFile(ctx, file, err); err != nil || file == nil {
		return mcpErrorResult(""), err
	}
	return mcpFileResult(name, contentType, file), nil
}
