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
	for _, label := range frame.Columns {
		frame.Widths = append(frame.Widths, float64(utf8.RuneCountInString(label)+10))
	}
	frame.Rows = rows
	workbook, err := xlsxexport.PandasXlsxwriter(frame, s.now())
	switch {
	case errors.Is(err, xlsxexport.ErrNotPorted):
		return nil, domain.ErrModuleResponsesNotPorted
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

// modulesExportColumns reads module.elements: element['type'] not in
// ['heading', 'paragraph', 'link'] adds element['props']['label'] and
// ['name']. Iterating a str or a dict yields str elements, which raise.
func modulesExportColumns(text *string) ([]modulesExportColumn, error) {
	value, err := dto.AuditStoredJSON(text)
	if err != nil {
		return nil, domain.ErrModuleResponsesNotPorted
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
			return nil, domain.ErrModuleResponsesNotPorted // indexing a str, list or scalar
		}
		label, hasLabel := props.Lookup("label")
		name, hasName := props.Lookup("name")
		if !hasLabel || !hasName {
			return nil, domain.ErrModulesExportServer
		}
		text, ok := label.(string)
		if !ok {
			// len() of None, a number or a bool raises; list and dict labels are not ported.
			switch label.(type) {
			case []any, domain.RequestObject:
				return nil, domain.ErrModuleResponsesNotPorted
			}
			return nil, domain.ErrModulesExportServer
		}
		columns = append(columns, modulesExportColumn{label: text, name: name})
	}
	return columns, nil
}

func (s *ModulesExport) row(ctx context.Context, columns []modulesExportColumn, response domain.ModuleExportResponse) ([]xlsxexport.Value, error) {
	row := []xlsxexport.Value{}
	if len(columns) > 0 {
		value, err := dto.AuditStoredJSON(&response.Response)
		if err != nil {
			return nil, domain.ErrModuleResponsesNotPorted
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
			if err != nil {
				return xlsxexport.Value{}, domain.ErrModuleResponsesNotPorted // beyond int64
			}
			return xlsxexport.IntValue(number), nil
		}
		number, err := strconv.ParseFloat(text, 64)
		if err != nil && !errors.Is(err, strconv.ErrRange) {
			return xlsxexport.Value{}, domain.ErrModuleResponsesNotPorted
		}
		return xlsxexport.FloatValue(number), nil
	case []any, domain.RequestObject:
		return xlsxexport.ObjectValue(dto.PythonStr(typed)), nil
	}
	return xlsxexport.Value{}, domain.ErrModuleResponsesNotPorted
}
