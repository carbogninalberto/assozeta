package service

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/xlsxexport"
)

// ModulesExport ports modules_response_export (modules_views.py).
type ModulesExport struct {
	store domain.ModulesExportStore
	now   func() time.Time
}

func NewModulesExport(store domain.ModulesExportStore, now func() time.Time) *ModulesExport {
	return &ModulesExport{store: store, now: now}
}

var modulesExportStandard = []string{"Data creazione", "Progressivo risposta", "Posizione in coda", "Utente", "Pagamento", "Approvato",
	"Allegati"}

// modulesExportColumn is one response column of the module's elements.
type modulesExportColumn struct {
	label string
	width int // len(label): the characters of a string, the items of a list or object
	name  any
}

// Export follows modules_response_export: is_valid_uuid, the responses of
// the module within request.user.sport_association (404 without any), the
// columns of every element that is not a heading, paragraph or link, one
// row per response and the xlsxwriter workbook (sheet 'Risposte',
// set_column(i, i, len(label) + 10)).
func (s *ModulesExport) Export(ctx context.Context, identity domain.ResolvedIdentity, raw string) (any, error) {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return nil, err
	}
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return nil, &domain.PermissionDenied{Detail: "User is not a sport association"}
	}
	association, err := s.store.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	module := id.String()
	first, err := s.store.FirstResponse(ctx, module, association)
	if err != nil {
		return nil, err
	}
	if first == nil {
		return nil, domain.ErrModuleResponseNotFound
	}
	title, elementsText, err := s.store.Module(ctx, first.ModuleID)
	if err != nil {
		return nil, err
	}
	columns, err := modulesExportColumns(elementsText)
	if err != nil {
		return nil, err
	}
	responses, err := s.store.Responses(ctx, module, association)
	if err != nil {
		return nil, err
	}
	rows := make([][]xlsxexport.Value, 0, len(responses))
	for _, response := range responses {
		row, err := s.row(ctx, columns, response)
		if err != nil {
			return nil, err
		}
		rows = append(rows, row)
	}
	frame := xlsxexport.Frame{Sheet: "Risposte"}
	for _, column := range columns {
		frame.Columns = append(frame.Columns, column.label)
	}
	frame.Columns = append(frame.Columns, modulesExportStandard...)
	for index, label := range frame.Columns {
		width := utf8.RuneCountInString(label)
		if index < len(columns) {
			width = columns[index].width
		}
		frame.Widths = append(frame.Widths, float64(width+10))
	}
	frame.Rows = rows
	workbook, err := xlsxexport.PandasXlsxwriter(frame, s.now())
	switch {
	case errors.Is(err, refusal.ErrAppState):
		return nil, err // an input no view builds (xlsxexport.ErrUnexpectedInput)
	case err != nil:
		return nil, domain.ErrModulesExportServer
	}
	name := "None"
	if title != nil {
		name = *title
	}
	now := s.now().UTC()
	filename := "[" + now.Format("2006-01-02") + "] Risposte al modulo (" + name + ").xls"
	return domain.RequestObject{{Name: "data", Value: domain.RequestObject{{Name: "file", Value: base64.StdEncoding.EncodeToString(workbook)}, {Name: "filename", Value: filename},
		{Name: "type", Value: "xlsx"}}}}, nil
}

// modulesExportElementsJSONDepth is the deepest total JSON nesting (as
// dto.AuditJSONDepth counts it: '[]' is 1) the export loads from
// module.elements. Measured on the parity harness's Django (CPython 3.13.16):
// 9933 levels load and 9934 raise RecursionError in json.loads, which the view
// does not handle (the 500 page). Owner decision X15: the band, not a refusal.
const modulesExportElementsJSONDepth = 9933

// modulesExportElements is json.loads of the stored module.elements text (the
// response column keeps its own reader): a value past the interpreter's
// recursion band is the export's unhandled exception.
func modulesExportElements(text *string) (any, error) {
	if text == nil {
		return nil, nil
	}
	value, err := dto.ParseStoredJSONDepth(*text, modulesExportElementsJSONDepth)
	if err != nil {
		return nil, domain.ErrModulesExportServer
	}
	return value, nil
}

// modulesExportColumns reads module.elements: element['type'] not in
// ['heading', 'paragraph', 'link'] adds element['props']['label'] and
// ['name']. Iterating a str or a dict yields str elements, which raise.
func modulesExportColumns(text *string) ([]modulesExportColumn, error) {
	value, err := modulesExportElements(text)
	if err != nil {
		return nil, err
	}
	var elements []any
	switch typed := value.(type) {
	case []any:
		elements = typed
	case string:
		if typed != "" {
			return nil, domain.ErrModulesExportServer // str indices must be integers
		}
	case domain.RequestObject:
		if len(typed) > 0 {
			return nil, domain.ErrModulesExportServer
		}
	default:
		return nil, domain.ErrModulesExportServer // None or a number is not iterable
	}
	columns := []modulesExportColumn{}
	for _, item := range elements {
		element, ok := item.(domain.RequestObject)
		if !ok {
			return nil, domain.ErrModulesExportServer
		}
		kind, ok := element.Lookup("type")
		if !ok {
			return nil, domain.ErrModulesExportServer // KeyError
		}
		if kind == "heading" || kind == "paragraph" || kind == "link" {
			continue
		}
		propsValue, ok := element.Lookup("props")
		if !ok {
			return nil, domain.ErrModulesExportServer
		}
		props, ok := propsValue.(domain.RequestObject)
		if !ok {
			return nil, domain.ErrModulesExportServer // indexing a str, list or scalar
		}
		label, hasLabel := props.Lookup("label")
		name, hasName := props.Lookup("name")
		if !hasLabel || !hasName {
			return nil, domain.ErrModulesExportServer
		}
		text, ok := label.(string)
		width := utf8.RuneCountInString(text)
		if !ok {
			// len() of None, a number or a bool raises; a list or an object is
			// written as its text and sized by its item count.
			switch typed := label.(type) {
			case []any:
				text, width = dto.PythonRepr(typed), len(typed)
			case domain.RequestObject:
				text, width = dto.PythonRepr(typed), modulesExportDistinct(typed)
			default:
				return nil, domain.ErrModulesExportServer
			}
		}
		columns = append(columns, modulesExportColumn{label: text, width: width, name: name})
	}
	return columns, nil
}

// modulesExportResponseJSONDepth is the deepest total JSON nesting (as
// dto.AuditJSONDepth counts it: '[]' is 1) the export loads from a response
// column. Measured on {"nome": "Anna", "deep": <nested objects>} with the parity
// harness's Django (CPython 3.13.16): 9935 levels load and 9936 raise
// RecursionError in psycopg2's json.loads, which the view does not handle (the
// 500 page). Owner decision X15: the band, not a refusal.
const modulesExportResponseJSONDepth = 9935

// modulesExportResponse is json.loads of the stored response text: a value past
// the interpreter's recursion band is the export's unhandled exception.
func modulesExportResponse(text *string) (any, error) {
	value, err := dto.AuditStoredJSONAt(text, modulesExportResponseJSONDepth)
	if err != nil {
		return nil, domain.ErrModulesExportServer
	}
	return value, nil
}

func (s *ModulesExport) row(ctx context.Context, columns []modulesExportColumn, response domain.ModuleExportResponse) ([]xlsxexport.Value, error) {
	row := []xlsxexport.Value{}
	if len(columns) > 0 {
		value, err := modulesExportResponse(&response.Response)
		if err != nil {
			return nil, err
		}
		object, ok := value.(domain.RequestObject)
		if !ok {
			return nil, domain.ErrModulesExportServer // .keys() of a list, str, number or None
		}
		for _, column := range columns {
			switch column.name.(type) {
			case []any, domain.RequestObject:
				return nil, domain.ErrModulesExportServer // unhashable
			}
			key, isText := column.name.(string)
			item, found := object.Lookup(key)
			if !isText || !found {
				row = append(row, xlsxexport.StrValue("-"))
				continue
			}
			cell, err := modulesExportValue(item)
			if err != nil {
				return nil, err
			}
			row = append(row, cell)
		}
	}
	optionalInt := func(value *int64) xlsxexport.Value {
		if value == nil {
			return xlsxexport.StrValue("-")
		}
		return xlsxexport.IntValue(*value)
	}
	user := xlsxexport.StrValue("-")
	if response.UserID != nil {
		found, err := s.store.User(ctx, *response.UserID)
		if err != nil {
			return nil, err
		}
		if found != nil {
			text := func(value *string) string {
				if value == nil {
					return "None"
				}
				return *value
			}
			// get_full_name: '%s %s' % (first_name, last_name), stripped.
			user = xlsxexport.StrValue(strings.TrimFunc(text(found.FirstName)+" "+text(found.LastName), dto.PythonIsSpace))
		}
	}
	payment := xlsxexport.StrValue("-")
	if response.PaymentID != nil {
		paid, err := s.store.PaymentPaid(ctx, *response.PaymentID)
		if err != nil {
			return nil, err
		}
		payment = xlsxexport.StrValue("Non pagato")
		if paid {
			payment = xlsxexport.StrValue("Pagato")
		}
	}
	approved := "Non approvata"
	if response.Approved {
		approved = "Approvata"
	}
	names, err := s.store.Attachments(ctx, response.ID)
	if err != nil {
		return nil, err
	}
	return append(row, xlsxexport.StrValue(response.Created.UTC().Format("02/01/2006 15:04")), optionalInt(response.Number),
		optionalInt(response.Queue), user, payment, xlsxexport.StrValue(approved), xlsxexport.StrValue(strings.Join(names, ","))), nil
}

// modulesExportValue is the Python value json.loads gave a response field.
func modulesExportValue(value any) (xlsxexport.Value, error) {
	switch typed := value.(type) {
	case nil:
		return xlsxexport.NoneValue(), nil
	case string:
		return xlsxexport.StrValue(typed), nil
	case bool:
		return xlsxexport.BoolValue(typed), nil
	case json.Number:
		text := typed.String()
		if !strings.ContainsAny(text, ".eE") {
			number, err := strconv.ParseInt(text, 10, 64)
			if err == nil {
				return xlsxexport.IntValue(number), nil
			}
			if !errors.Is(err, strconv.ErrRange) {
				// the text comes from a JSON parser, always a valid integer
				return xlsxexport.Value{}, domain.ErrModuleResponsesUnreachable
			}
			// A Python int beyond int64: pandas reads it as float64 (or keeps
			// it an int) and xlsxwriter writes float(number) with '%.16G'.
			return xlsxexport.BigIntValue(text), nil
		}
		number, err := strconv.ParseFloat(text, 64)
		if err != nil && !errors.Is(err, strconv.ErrRange) {
			// the text comes from a JSON parser, always a valid number
			return xlsxexport.Value{}, domain.ErrModuleResponsesUnreachable
		}
		return xlsxexport.FloatValue(number), nil
	case []any, domain.RequestObject:
		return xlsxexport.ObjectValue(dto.PythonStr(typed)), nil
	}
	// a type outside the decoded JSON types
	return xlsxexport.Value{}, domain.ErrModuleResponsesUnreachable
}

// modulesExportDistinct is len() of a JSON object: repeated keys count once.
func modulesExportDistinct(object domain.RequestObject) int {
	seen := map[string]bool{}
	for _, field := range object {
		seen[field.Name] = true
	}
	return len(seen)
}
