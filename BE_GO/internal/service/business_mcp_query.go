package service

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// The QueryBuilder tools of BE/application/mcp_server/server.py
// (get_schema, query_data, count_data, get_field_values, aggregate_data),
// export_data (CSV), export_multi_sheet and get_attendance_matrix (their
// results before any XLSX/PDF file is built).

// BusinessMCPQueryStore runs the read-only QueryBuilder statements.
type BusinessMCPQueryStore interface {
	ReportIntegration(ctx context.Context) (bool, *string, error)
	RunReportStatements(ctx context.Context, timeoutMS int64, statements []domain.ReportStatement) ([][][]*string, error)
}

func mcpObj(pairs ...any) domain.RequestObject {
	object := make(domain.RequestObject, 0, len(pairs)/2)
	for i := 0; i < len(pairs); i += 2 {
		object = append(object, domain.RequestField{Name: pairs[i].(string), Value: pairs[i+1]})
	}
	return object
}

func mcpErrorResult(message string) domain.RequestObject { return mcpObj("error", message) }

// mcpToolOutcome maps a planning/execution error to the call_tool result:
// exceptions are {'error': str(e)}, not-ported inputs are refused.
func mcpToolOutcome(ctx context.Context, err error) (any, error) {
	var raised *dto.MCPToolException
	if errors.As(err, &raised) {
		markMCPRaised(ctx)
		return mcpErrorResult(raised.Message), nil
	}
	var query *domain.ReportQueryError
	if raised, ok := ctx.Value(mcpRaisedKey{}).(*bool); ok && raised != nil && ctx.Value(reportRunKey{}) != nil &&
		(errors.Is(err, dto.ErrReportFailed) || errors.As(err, &query)) {
		*raised = true // a saved-report run answers every exception alike
		return mcpErrorResult(""), nil
	}
	if errors.Is(err, dto.ErrReportNotPorted) || errors.Is(err, dto.ErrReportFailed) {
		return nil, ErrBusinessMCPNotPorted
	}
	if errors.As(err, &query) {
		return nil, ErrBusinessMCPNotPorted // database error text (SQL in the message)
	}
	return nil, err
}

// mcpRaisedResult is an exception raised inside a tool function. A
// saved-report run answers every exception alike; the MCP transport returns
// str(e), so an exception whose text is not given (message "") stays refused
// there.
func mcpRaisedResult(ctx context.Context, message string) (any, error) {
	if message == "" && ctx.Value(reportRunKey{}) == nil {
		return nil, ErrBusinessMCPNotPorted
	}
	markMCPRaised(ctx)
	return mcpErrorResult(message), nil
}

// runPlan executes a planned QueryBuilder call and renders its result.
func (t *BusinessMCPTools) runPlan(ctx context.Context, plan dto.ReportPlan, timeoutMS int64) (any, error) {
	if plan.Immediate != nil {
		return mcpErrorResult(plan.Immediate.Message), nil
	}
	if plan.Value != nil {
		return plan.Value, nil
	}
	results, err := t.store.RunReportStatements(ctx, timeoutMS, plan.Statements)
	if err != nil {
		return mcpToolOutcome(ctx, err)
	}
	value, err := plan.Render(results)
	if err != nil {
		return mcpToolOutcome(ctx, err)
	}
	return value, nil
}

// query plans and runs one QueryBuilder method with kwargs.
func (t *BusinessMCPTools) query(ctx context.Context, tool string, kwargs domain.RequestObject) (any, error) {
	var timeout int64
	plan, err := dto.PlanMCPToolGroup(tool, kwargs, t.association, t.group, func() (dto.ReportSettings, error) {
		settings, err := loadReportSettings(ctx, t.store)
		timeout = settings.QueryTimeout * 1000
		return settings, err
	})
	if err != nil {
		return mcpToolOutcome(ctx, err)
	}
	return t.runPlan(ctx, plan, timeout)
}

// kwargs picks the keyword arguments a tool function forwards to QueryBuilder.
func mcpKwargs(arguments domain.RequestObject, names ...string) domain.RequestObject {
	selected := domain.RequestObject{}
	for _, name := range names {
		if value, present := arguments.Lookup(name); present {
			selected = append(selected, domain.RequestField{Name: name, Value: value})
		}
	}
	return selected
}

var mcpQueryArguments = []string{"model_name", "fields", "filters", "exclude", "or_filters", "joins", "order_by", "limit", "offset",
	"include_archived"}

func (t *BusinessMCPTools) schema(ctx context.Context, arguments domain.RequestObject) (any, error) {
	value, _ := arguments.Lookup("model_name")
	plan, err := dto.MCPSchema(value)
	if err != nil {
		return mcpToolOutcome(ctx, err)
	}
	if plan.Immediate != nil {
		return mcpErrorResult(plan.Immediate.Message), nil
	}
	return plan.Value, nil
}

// ---------------------------------------------------------------------------
// export_data
// ---------------------------------------------------------------------------

// mcpExportBlacklist is exporters.EXPORT_FIELD_BLACKLIST.
var mcpExportBlacklist = map[string]bool{"sport_association_id": true, "sport_association": true, "associate_id": true,
	"subscription_id": true, "course_id": true, "course_subscription_id": true, "payment_id": true, "payment_category_id": true,
	"invoice_id": true, "invoice_row_id": true, "medical_id": true, "instructor_id": true, "balance_sheet_id": true, "group_id": true,
	"carnet_id": true, "carnet_subscription_id": true, "module_id": true, "module_response_id": true, "attendance_registry_id": true,
	"attendance_day_id": true, "reminder_id": true, "custom_account_id": true, "tag_id": true, "course_tag_id": true,
	"document_id": true, "user_id": true, "connected_user_id": true, "deleted": true, "disabled": true, "is_active": true,
	"is_superuser": true, "is_staff": true, "updated_at": true}

var (
	mcpExportDate = regexp.MustCompile(`^(\d{4})-(\d{2})-(\d{2})$`)
)

// formatExportValue is exporters.format_value on a serialized value.
func mcpFormatExportValue(value any) (any, error) {
	switch typed := value.(type) {
	case nil:
		return "", nil
	case bool:
		if typed {
			return "Vero", nil
		}
		return "Falso", nil
	case string:
		points := []rune(typed)
		if len(points) >= 19 && points[4] == '-' && points[10] == 'T' {
			if formatted, ok := mcpISODateTime(typed); ok {
				return formatted, nil
			}
		}
		if len(points) == 10 && points[4] == '-' && points[7] == '-' {
			// date.fromisoformat reads the UTF-8 bytes: only ten ASCII characters parse.
			if match := mcpExportDate.FindStringSubmatch(typed); match != nil {
				year, _ := strconv.Atoi(match[1])
				month, _ := strconv.Atoi(match[2])
				day, _ := strconv.Atoi(match[3])
				if year >= 1 && month >= 1 && month <= 12 && day >= 1 &&
					day <= time.Date(year, time.Month(month)+1, 0, 0, 0, 0, 0, time.UTC).Day() {
					return match[3] + "/" + match[2] + "/" + match[1], nil
				}
			}
		}
	}
	return value, nil
}

// csvField is csv.writer's str() of a field (None is the empty text).
func mcpCSVField(value any) (string, error) {
	switch typed := value.(type) {
	case string:
		return typed, nil
	case nil:
		return "", nil
	case json.Number, bool, domain.RequestObject, []any:
		return dto.PythonStr(typed), nil
	}
	return "", ErrBusinessMCPNotPorted
}

// csvRow is csv.writer(dialect='excel').writerow.
func mcpCSVRow(out *bytes.Buffer, fields []string) error {
	for i, field := range fields {
		if strings.ContainsRune(field, 0) {
			return ErrBusinessMCPNotPorted
		}
		if i > 0 {
			out.WriteByte(',')
		}
		if strings.ContainsAny(field, ",\"\r\n") || (field == "" && len(fields) == 1) {
			out.WriteString(`"` + strings.ReplaceAll(field, `"`, `""`) + `"`)
		} else {
			out.WriteString(field)
		}
	}
	out.WriteString("\r\n")
	return nil
}

// exportCSV is CSVExporter.export (format_export_data, UTF-8 BOM).
func mcpExportCSV(data []any, labels domain.RequestObject) ([]byte, error) {
	var out bytes.Buffer
	out.WriteString("\xef\xbb\xbf")
	first := data[0].(domain.RequestObject)
	var columns []string
	for _, field := range first {
		if !mcpExportBlacklist[field.Name] {
			columns = append(columns, field.Name)
		}
	}
	headers := make([]string, len(columns))
	for i, column := range columns {
		headers[i] = column
		if label, present := labels.Lookup(column); present {
			text, err := mcpCSVField(label)
			if err != nil {
				return nil, err
			}
			headers[i] = text
		}
	}
	if err := mcpCSVRow(&out, headers); err != nil {
		return nil, err
	}
	for _, item := range data {
		row := item.(domain.RequestObject)
		fields := make([]string, len(columns))
		for i, column := range columns {
			value, _ := row.Lookup(column)
			formatted, err := mcpFormatExportValue(value)
			if err != nil {
				return nil, err
			}
			text, err := mcpCSVField(formatted)
			if err != nil {
				return nil, err
			}
			fields[i] = text
		}
		if err := mcpCSVRow(&out, fields); err != nil {
			return nil, err
		}
	}
	return out.Bytes(), nil
}

func mcpLookupOr(arguments domain.RequestObject, name string, fallback any) any {
	if value, present := arguments.Lookup(name); present {
		return value
	}
	return fallback
}

func mcpTruthy(value any) bool { return dto.PythonTruthy(value) }

func (t *BusinessMCPTools) exportData(ctx context.Context, arguments domain.RequestObject) (any, error) {
	format := mcpLookupOr(arguments, "format", "csv")
	formatText, ok := format.(string)
	if !ok {
		// `format not in EXPORTERS`: a list or dict is not hashable.
		switch format.(type) {
		case []any:
			return mcpRaisedResult(ctx, "unhashable type: 'list'")
		case domain.RequestObject:
			return mcpRaisedResult(ctx, "unhashable type: 'dict'")
		}
		return mcpErrorResult("Unsupported format: " + dto.PythonStr(format) + ". Use: csv, xlsx, pdf"), nil
	}
	if formatText != "csv" && formatText != "xlsx" && formatText != "pdf" {
		return mcpErrorResult("Unsupported format: " + formatText + ". Use: csv, xlsx, pdf"), nil
	}
	query := mcpKwargs(arguments, "model_name", "fields", "filters", "exclude", "or_filters", "joins", "order_by", "limit",
		"include_archived")
	result, err := t.query(ctx, "query_data", query)
	if err != nil || result == nil {
		return result, err
	}
	object := result.(domain.RequestObject)
	if _, failed := object.Lookup("error"); failed {
		return object, nil
	}
	dataValue, _ := object.Lookup("data")
	data := dataValue.([]any)
	if len(data) == 0 {
		return mcpObj("error", "No data to export", "count", json.Number("0")), nil
	}
	if formatText != "csv" && !t.files {
		return nil, ErrBusinessMCPNotPorted // XLSX and PDF files are built for a saved-report run only
	}
	modelName, _ := arguments.Lookup("model_name")
	title := mcpLookupOr(arguments, "title", "")
	titleOrModel := title
	if !mcpTruthy(title) {
		titleOrModel = modelName
	}
	titleText, ok := titleOrModel.(string)
	if !ok {
		return mcpRaisedResult(ctx, "") // sanitize_text: re.sub on a value that is not a str
	}
	filename, err := SanitizeText(titleText, "-")
	if err != nil {
		return nil, ErrBusinessMCPNotPorted
	}
	labels := domain.RequestObject{}
	if value := mcpLookupOr(arguments, "column_labels", nil); mcpTruthy(value) {
		object, ok := value.(domain.RequestObject)
		if !ok {
			// .get on a value that is not a dict, once per kept column.
			if columns, _, _ := mcpExportColumns(data, nil); len(columns) > 0 {
				return mcpRaisedResult(ctx, "")
			}
		}
		labels = dto.PythonDict(object)
	}
	var file []byte
	contentType := "text/csv; charset=utf-8"
	switch formatText {
	case "csv":
		file, err = mcpExportCSV(data, labels)
	case "xlsx":
		contentType = reportXLSXType
		file, err = t.exportXLSX(ctx, data, labels, titleText)
	default:
		contentType = "application/pdf"
		file, err = t.exportPDF(ctx, data, labels, titleText, arguments)
	}
	if err != nil || file == nil {
		return mcpErrorResult(""), err // a nil file is an exception of the exporter (marked raised)
	}
	params := mcpObj("model_name", modelName, "format", formatText)
	for _, name := range []string{"title", "fields", "filters", "exclude", "or_filters", "joins", "order_by", "limit", "column_labels",
		"include_archived", "header_text", "footer_text", "text_before", "text_after"} {
		if value, _ := arguments.Lookup(name); mcpTruthy(value) {
			params = append(params, domain.RequestField{Name: name, Value: value})
		}
	}
	total, _ := object.Lookup("count")
	hint := strconv.Itoa(len(data)) + " righe"
	filters, _ := arguments.Lookup("filters")
	if mcpTruthy(filters) {
		count := 1
		switch typed := filters.(type) {
		case domain.RequestObject:
			count = len(dto.PythonDict(typed))
		case []any:
			count = len(typed)
		case string:
			count = utf8.RuneCountInString(typed)
		default:
			return nil, ErrBusinessMCPNotPorted
		}
		// f'{n} filtro{"i" if n > 1 else ""} applicato{"i" if n > 1 else ""}'
		plural := ""
		if count > 1 {
			plural = "i"
		}
		hint += ", " + strconv.Itoa(count) + " filtro" + plural + " applicato" + plural
	}
	fields := []any{mcpObj("key", "format", "type", "select", "label", "Formato", "options", []any{"csv", "xlsx", "pdf"})}
	if mcpTruthy(filters) {
		fields = append(fields, mcpObj("key", "filters", "type", "json", "label", "Filtri"))
	}
	return mcpObj("type", "export_ready",
		"filename", filename+"."+formatText,
		"content_type", contentType,
		"data_base64", base64.StdEncoding.EncodeToString(file),
		"row_count", json.Number(strconv.Itoa(len(data))),
		"total_count", total,
		"save_info", mcpObj("tool_name", "export_data", "params", params,
			"name", titleText+" - "+t.now().Format("02/01/2006"),
			"description", "Export "+titleText+" ("+strings.ToUpper(formatText)+") - "+hint,
			"description_hint", hint,
			"ui_config", mcpObj("fields", fields),
			"model_name", modelName)), nil
}

// ---------------------------------------------------------------------------
// export_multi_sheet
// ---------------------------------------------------------------------------

func (t *BusinessMCPTools) exportMultiSheet(ctx context.Context, arguments domain.RequestObject) (any, error) {
	sheets, _ := arguments.Lookup("sheets")
	if !mcpTruthy(sheets) {
		return mcpErrorResult("No sheets specified"), nil
	}
	// QueryBuilder(sport_association_id) reads the settings once.
	if _, err := loadReportSettings(ctx, t.store); err != nil {
		return mcpToolOutcome(ctx, err)
	}
	items, ok := sheets.([]any)
	if !ok {
		return mcpRaisedResult(ctx, "") // enumerate() of a number, or .get on a character or key
	}
	var prepared []reportSheetData
	for i, item := range items {
		spec, ok := item.(domain.RequestObject)
		if !ok {
			return mcpRaisedResult(ctx, "") // .get on a value that is not a dict
		}
		spec = dto.PythonDict(spec)
		modelName, _ := spec.Lookup("model_name")
		if !mcpTruthy(modelName) {
			return mcpErrorResult("Sheet " + strconv.Itoa(i+1) + ": model_name is required"), nil
		}
		query := mcpKwargs(spec, "model_name", "fields", "filters", "exclude", "or_filters", "joins", "order_by", "limit",
			"include_archived")
		result, err := t.query(ctx, "query_data", query)
		if err != nil {
			return nil, err
		}
		object := result.(domain.RequestObject)
		if message, failed := object.Lookup("error"); failed {
			// QueryBuilder raises (the dict only comes from call_tool's handler).
			return mcpErrorResult(message.(string)), nil
		}
		data, _ := object.Lookup("data")
		if len(data.([]any)) > 0 {
			prepared = append(prepared, reportSheetData{data: data.([]any), spec: spec, model: modelName})
		}
	}
	if len(prepared) == 0 {
		return mcpObj("error", "No data to export from any sheet", "count", json.Number("0")), nil
	}
	if !t.files {
		return nil, ErrBusinessMCPNotPorted // the XLSX workbook is built for a saved-report run only
	}
	return t.exportSheets(ctx, prepared, arguments)
}

// ---------------------------------------------------------------------------
// get_attendance_matrix
// ---------------------------------------------------------------------------

type attendanceCourse struct{ id, title *string }

type attendanceMatrix struct {
	course     *string
	id         string
	dates      []any
	rows       []attendanceRow
	totalDates int
}

type attendanceRow struct {
	name           string
	id             string
	present, total int
	marks          []string
}

func (t *BusinessMCPTools) rows(ctx context.Context, sql string, args []any, columns int) ([][]*string, error) {
	results, err := t.store.RunReportStatements(ctx, 0, []domain.ReportStatement{{SQL: sql, Args: args, Text: columns}})
	if err != nil {
		return nil, err
	}
	return results[0], nil
}

// attendanceDate is _parse_date (strptime '%Y-%m-%d'; failures are None).
func mcpAttendanceDate(value any) (string, bool, error) {
	if !mcpTruthy(value) {
		return "", false, nil
	}
	text, ok := value.(string)
	if !ok {
		return "", false, nil // strptime raises TypeError: no date
	}
	parsed, ok, uncertain := dto.MCPStrptimeDate(text)
	if uncertain {
		return "", false, ErrBusinessMCPNotPorted
	}
	return parsed, ok, nil
}

var attendanceCourseColumns = `"application_course"."course_id"::text, "application_course"."title"::text`

func (t *BusinessMCPTools) attendanceCourses(ctx context.Context, arguments domain.RequestObject) ([]attendanceCourse, any, error) {
	scan := func(rows [][]*string) []attendanceCourse {
		courses := make([]attendanceCourse, 0, len(rows))
		for _, row := range rows {
			courses = append(courses, attendanceCourse{id: row[0], title: row[1]})
		}
		return courses
	}
	if value, _ := arguments.Lookup("course_id"); mcpTruthy(value) {
		text, ok := value.(string)
		if !ok {
			return nil, nil, ErrBusinessMCPNotPorted
		}
		// Q(pk=..., sport_association_id=...) prepares pk first.
		id, err := dto.MCPUUIDArgument(text)
		if err != nil {
			return nil, nil, err
		}
		association, err := dto.MCPUUIDArgument(t.association)
		if err != nil {
			return nil, nil, err
		}
		rows, err := t.rows(ctx, `SELECT `+attendanceCourseColumns+` FROM "application_course" WHERE (NOT "application_course"."deleted" AND `+
			`"application_course"."course_id" = $1::uuid AND "application_course"."sport_association_id" = $2::uuid`+t.courseGroup()+`) LIMIT 21`,
			[]any{id, association}, 2)
		if err != nil {
			return nil, nil, err
		}
		if len(rows) == 0 {
			return nil, mcpErrorResult("Corso non trovato con ID: " + text), nil
		}
		return scan(rows), nil, nil
	}
	association, err := dto.MCPUUIDArgument(t.association)
	if err != nil {
		return nil, nil, err
	}
	if value, _ := arguments.Lookup("course_name"); mcpTruthy(value) {
		text, ok := value.(string)
		if !ok {
			return nil, nil, ErrBusinessMCPNotPorted
		}
		escaped := "%" + strings.NewReplacer(`\`, `\\`, "%", `\%`, "_", `\_`).Replace(text) + "%"
		where := ` FROM "application_course" WHERE (NOT "application_course"."deleted" AND "application_course"."sport_association_id" = $1::uuid` +
			` AND UPPER("application_course"."title"::text) LIKE UPPER($2) AND NOT "application_course"."deleted"` + t.courseGroup() + `)`
		count, err := t.rows(ctx, `SELECT COUNT(*)::text`+where, []any{association, escaped}, 1)
		if err != nil {
			return nil, nil, err
		}
		switch *count[0][0] {
		case "0":
			return nil, mcpErrorResult("Nessun corso trovato con nome: " + text), nil
		case "1":
			rows, err := t.rows(ctx, `SELECT `+attendanceCourseColumns+where+` ORDER BY "application_course"."course_id" ASC LIMIT 1`,
				[]any{association, escaped}, 2)
			if err != nil {
				return nil, nil, err
			}
			return scan(rows), nil, nil
		}
		rows, err := t.rows(ctx, `SELECT `+attendanceCourseColumns+where+` LIMIT 10`, []any{association, escaped}, 2)
		if err != nil {
			return nil, nil, err
		}
		matches := []any{}
		for _, course := range scan(rows) {
			matches = append(matches, mcpObj("course_id", *course.id, "title", mcpOptional(course.title)))
		}
		return nil, mcpObj("error", "Trovati piu' corsi con quel nome. Specifica meglio.", "matches", matches), nil
	}
	rows, err := t.rows(ctx, `SELECT `+attendanceCourseColumns+` FROM "application_course" WHERE (NOT "application_course"."deleted" AND `+
		`"application_course"."course_id" IN (SELECT DISTINCT U0."course_id" FROM "application_attendanceregistry" U0 `+
		`INNER JOIN "application_course" U1 ON (U0."course_id" = U1."course_id") WHERE (U1."sport_association_id" = $1::uuid AND NOT U1."deleted")) `+
		`AND NOT "application_course"."deleted"`+t.courseGroup()+`) ORDER BY "application_course"."title" ASC`, []any{association}, 2)
	if err != nil {
		return nil, nil, err
	}
	if len(rows) == 0 {
		return nil, mcpErrorResult("Nessun corso con registro presenze trovato."), nil
	}
	return scan(rows), nil, nil
}

func mcpOptional(text *string) any {
	if text == nil {
		return nil
	}
	return *text
}

// attendanceIDs is the presence set of one day's attendees.
func mcpAttendanceIDs(text *string) (map[string]bool, error) {
	present := map[string]bool{}
	if text == nil {
		return present, nil
	}
	parsed, err := dto.ParseReferenceJSON([]byte(*text))
	if err != nil {
		return nil, err
	}
	if !mcpTruthy(parsed) {
		return present, nil
	}
	var items []any
	switch typed := parsed.(type) {
	case []any:
		items = typed
	case domain.RequestObject:
		for _, field := range typed {
			items = append(items, field.Name)
		}
	case string:
		for _, r := range typed {
			items = append(items, string(r))
		}
	default:
		return nil, ErrBusinessMCPNotPorted
	}
	for _, item := range items {
		value := item
		if object, ok := item.(domain.RequestObject); ok {
			value = ""
			if id, present := dto.PythonDict(object).Lookup("course_subscription_id"); present {
				value = id
			}
		}
		var id string
		switch typed := value.(type) {
		case string:
			id = typed
		case nil:
			id = "None"
		case bool, json.Number:
			id = dto.PythonStr(typed)
		default:
			return nil, ErrBusinessMCPNotPorted
		}
		if id != "" {
			present[id] = true
		}
	}
	return present, nil
}

// pythonLowerSafe reports names whose str.lower() Go reproduces.
func mcpLowerSafe(text string) bool {
	return !strings.ContainsAny(text, "İΣ")
}

func (t *BusinessMCPTools) attendanceCourseMatrix(ctx context.Context, course attendanceCourse, from, to string) (*attendanceMatrix, error) {
	exists, err := t.rows(ctx, `SELECT 1::text FROM "application_attendanceregistry" WHERE "application_attendanceregistry"."course_id" = $1::uuid LIMIT 1`,
		[]any{*course.id}, 1)
	if err != nil || len(exists) == 0 {
		return nil, err
	}
	args := []any{*course.id}
	where := `"application_attendanceday"."attendance_registry_id" IN (SELECT U0."attendance_registry_id" FROM "application_attendanceregistry" U0 WHERE U0."course_id" = $1::uuid)`
	if from != "" {
		args = append(args, from)
		where += ` AND ("application_attendanceday"."date" AT TIME ZONE 'UTC')::date >= $` + strconv.Itoa(len(args)) + `::date`
	}
	if to != "" {
		args = append(args, to)
		where += ` AND ("application_attendanceday"."date" AT TIME ZONE 'UTC')::date <= $` + strconv.Itoa(len(args)) + `::date`
	}
	days, err := t.rows(ctx, `SELECT to_char("application_attendanceday"."date" AT TIME ZONE 'UTC', 'DD/MM/YYYY'), `+
		`"application_attendanceday"."attendees"::text FROM "application_attendanceday" WHERE (`+where+`) ORDER BY "application_attendanceday"."date" ASC`,
		args, 2)
	if err != nil || len(days) == 0 {
		return nil, err
	}
	subscriptions, err := t.rows(ctx, `SELECT "application_coursesubscription"."course_subscription_id"::text, `+
		`"application_associate"."first_name"::text, "application_associate"."last_name"::text FROM "application_coursesubscription" `+
		`LEFT OUTER JOIN "application_subscription" ON ("application_coursesubscription"."subscription_id" = "application_subscription"."subscription_id") `+
		`LEFT OUTER JOIN "application_associate" ON ("application_subscription"."associate_id" = "application_associate"."associate_id") `+
		`WHERE (NOT "application_coursesubscription"."deleted" AND "application_coursesubscription"."course_id" = $1::uuid AND `+
		`NOT "application_coursesubscription"."deleted")`, []any{*course.id}, 3)
	if err != nil {
		return nil, err
	}
	names := map[string]string{}
	var order []string
	for _, row := range subscriptions {
		id := *row[0]
		first, last := "", ""
		if row[1] != nil {
			first = *row[1]
		}
		if row[2] != nil {
			last = *row[2]
		}
		name := dto.PythonStrip(last + " " + first)
		if name == "" {
			name = id
		}
		if !mcpLowerSafe(name) {
			return nil, ErrBusinessMCPNotPorted
		}
		if _, seen := names[id]; !seen {
			order = append(order, id)
		}
		names[id] = name
	}
	matrix := &attendanceMatrix{course: course.title, id: *course.id, totalDates: len(days)}
	var present []map[string]bool
	for _, day := range days {
		label := "None"
		if day[0] != nil {
			label = *day[0]
		}
		matrix.dates = append(matrix.dates, label)
		ids, err := mcpAttendanceIDs(day[1])
		if err != nil {
			return nil, err
		}
		present = append(present, ids)
	}
	sort.SliceStable(order, func(i, j int) bool { return strings.ToLower(names[order[i]]) < strings.ToLower(names[order[j]]) })
	for _, id := range order {
		row := attendanceRow{name: names[id], id: id, total: len(days)}
		for _, ids := range present {
			mark := ""
			if ids[id] {
				row.present++
				mark = "P"
			}
			row.marks = append(row.marks, mark)
		}
		matrix.rows = append(matrix.rows, row)
	}
	return matrix, nil
}

func (t *BusinessMCPTools) attendance(ctx context.Context, arguments domain.RequestObject) (any, error) {
	courses, failure, err := t.attendanceCourses(ctx, arguments)
	if err != nil {
		return mcpToolOutcome(ctx, err)
	}
	if failure != nil {
		return failure, nil
	}
	fromValue, _ := arguments.Lookup("date_from")
	toValue, _ := arguments.Lookup("date_to")
	from, _, err := mcpAttendanceDate(fromValue)
	if err != nil {
		return nil, err
	}
	to, _, err := mcpAttendanceDate(toValue)
	if err != nil {
		return nil, err
	}
	exporting := mcpTruthy(mcpLookupOr(arguments, "export", false))
	note := "Anteprima dei primi 10 iscritti. Usa export=true per generare il file Excel completo."
	if len(courses) == 1 {
		matrix, err := t.attendanceCourseMatrix(ctx, courses[0], from, to)
		if err != nil {
			return mcpToolOutcome(ctx, err)
		}
		if matrix == nil {
			return mcpObj("error", "Nessuna giornata di presenza trovata per questo corso nel periodo indicato.",
				"course", mcpOptional(courses[0].title)), nil
		}
		if exporting {
			return t.attendanceFile(ctx, []*attendanceMatrix{matrix}, false, arguments)
		}
		preview := []any{}
		for i, row := range matrix.rows {
			if i == 10 {
				break
			}
			preview = append(preview, mcpObj("name", row.name, "present_count", json.Number(strconv.Itoa(row.present)),
				"total_days", json.Number(strconv.Itoa(row.total))))
		}
		dates := matrix.dates
		if dates == nil {
			dates = []any{}
		}
		return mcpObj("course", mcpOptional(matrix.course), "dates", dates,
			"total_enrolled", json.Number(strconv.Itoa(len(matrix.rows))),
			"total_dates", json.Number(strconv.Itoa(matrix.totalDates)),
			"preview", preview, "note", note), nil
	}
	var matrices []*attendanceMatrix
	enrolled, dates := 0, 0
	for _, course := range courses {
		matrix, err := t.attendanceCourseMatrix(ctx, course, from, to)
		if err != nil {
			return mcpToolOutcome(ctx, err)
		}
		if matrix != nil {
			matrices = append(matrices, matrix)
			enrolled += len(matrix.rows)
			dates += matrix.totalDates
		}
	}
	if len(matrices) == 0 {
		return mcpErrorResult("Nessun dato di presenza trovato per nessun corso nel periodo indicato."), nil
	}
	if exporting {
		return t.attendanceFile(ctx, matrices, true, arguments)
	}
	previews := []any{}
	for _, matrix := range matrices {
		previews = append(previews, mcpObj("course", mcpOptional(matrix.course), "total_enrolled", json.Number(strconv.Itoa(len(matrix.rows))),
			"total_dates", json.Number(strconv.Itoa(matrix.totalDates))))
	}
	return mcpObj("multi", true, "courses", previews,
		"summary", mcpObj("total_courses", json.Number(strconv.Itoa(len(matrices))), "total_enrolled", json.Number(strconv.Itoa(enrolled)),
			"total_dates", json.Number(strconv.Itoa(dates))),
		"note", "Usa export=true per generare un unico file Excel con tutti i corsi (include colonna Corso)."), nil
}
