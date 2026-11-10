package service

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pdfexport"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/xlsxexport"
)

// CourseSubscriptionExport is list's POST after get_queryset: the handler
// reads request.data and calls Export with it.
type CourseSubscriptionExport struct {
	query domain.CourseSubscriptionQuery
}

func (e *CourseSubscriptionExport) Error() string { return "course subscription export" }

// courseSubscriptionExportLessons is the carnet's lessons text:
// lessons_left = cs.meta.get('lessons_left', total) if cs.meta else total,
// then f"{lessons_left}/{total}" if total else ”.
func courseSubscriptionExportLessons(meta *string, total int64) (string, error) {
	left := strconv.FormatInt(total, 10)
	if meta != nil {
		decoder := json.NewDecoder(bytes.NewReader([]byte(*meta)))
		decoder.UseNumber()
		var value any
		if err := decoder.Decode(&value); err != nil {
			return "", err
		}
		switch typed := value.(type) {
		case nil:
		case map[string]any:
			if stored, present := typed["lessons_left"]; present {
				switch number := stored.(type) {
				case nil:
					left = "None"
				case string:
					left = number
				case bool:
					left = "False"
					if number {
						left = "True"
					}
				case json.Number:
					// An integer as written, a float with its repr.
					left = dto.PythonStr(number)
				default:
					return "", ErrCourseSubscriptionExportUnsupported // a list or dict
				}
			}
		case string:
			if typed != "" {
				return "", domain.ErrCourseSubscriptionServer // 'str' has no get
			}
		case bool:
			if typed {
				return "", domain.ErrCourseSubscriptionServer
			}
		case json.Number:
			if number, err := typed.Float64(); err != nil || number != 0 {
				return "", domain.ErrCourseSubscriptionServer
			}
		case []any:
			if len(typed) > 0 {
				return "", domain.ErrCourseSubscriptionServer
			}
		}
	}
	if total == 0 {
		return "", nil
	}
	return left + "/" + strconv.FormatInt(total, 10), nil
}

// Export is current_view_course_subscription(sport_association,
// request.data, queryset, title="Iscrizioni ai corsi"): data['format'] must
// be 'pdf' or 'excel' (the PDF is not ported). 'excel' is
// generate_xlsx_from_data of Cognome, Nome, Nato il, Email, Telefono, Corso,
// Data creazione, Tags, a Carnet column when a listed row has an enabled
// carnet subscription, and one column per Tags of the association. The
// carnet loop rebinds the function's title, so the filename carries the last
// carnet title it handled. Every exception is the view's 500.
func (s *CourseSubscriptions) Export(ctx context.Context, export *CourseSubscriptionExport, data any) (map[string]any, error) {
	format, err := dto.PythonIndex(data, "format")
	if err != nil {
		return nil, domain.ErrCourseSubscriptionServer // KeyError or TypeError
	}
	switch format {
	case "excel":
	case "pdf":
	default:
		return nil, domain.ErrCourseSubscriptionServer // Exception('Format not valid')
	}
	today := s.now().UTC()
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	rows, err := tx.ExportRows(ctx, export.query)
	if err != nil {
		return nil, err
	}
	ids, listed := make([]string, 0, len(rows)), map[string]bool{}
	for _, row := range rows {
		ids = append(ids, row.ID)
		listed[row.ID] = true
	}
	carnets, err := tx.ExportCarnets(ctx, ids)
	if err != nil {
		return nil, err
	}
	holders, seen := []string{}, map[string]bool{}
	for _, carnet := range carnets {
		if !seen[carnet.ID] {
			seen[carnet.ID] = true
			holders = append(holders, carnet.ID)
		}
	}
	links, err := tx.ExportCarnetLinks(ctx, holders)
	if err != nil {
		return nil, err
	}
	linked := map[string][]string{}
	for _, link := range links {
		linked[link.CarnetSubscriptionID] = append(linked[link.CarnetSubscriptionID], link.CourseSubscriptionID)
	}
	title := "Iscrizioni ai corsi"
	lookup := map[string][]string{}
	for _, carnet := range carnets {
		for _, row := range linked[carnet.ID] {
			if !listed[row] {
				continue
			}
			if _, present := lookup[row]; !present {
				lookup[row] = []string{}
			}
			if carnet.CarnetID == nil {
				continue
			}
			title = "Carnet"
			if carnet.Title != nil && *carnet.Title != "" {
				title = *carnet.Title
			}
			total := int64(0)
			if carnet.Lessons != nil {
				total = *carnet.Lessons
			}
			lessons, err := courseSubscriptionExportLessons(carnet.Meta, total)
			if err != nil {
				return nil, err
			}
			display := title + " (" + printingDate(carnet.CreationDate.UTC()) + ")"
			if lessons != "" {
				display += " - " + lessons
			}
			lookup[row] = append(lookup[row], display)
		}
	}
	columns := []string{"Cognome", "Nome", "Nato il", "Email", "Telefono", "Corso", "Data creazione", "Tags"}
	if len(lookup) > 0 {
		columns = append(columns, "Carnet")
	}
	cells := make([][]xlsxexport.Value, len(columns))
	owned := map[string][]domain.CourseSubscriptionExportTag{}
	for _, row := range rows {
		if row.SubscriptionID == nil || row.AssociateID == nil || row.BornDate == nil {
			return nil, domain.ErrCourseSubscriptionServer // AttributeError on None
		}
		tags, cached := owned[*row.SubscriptionID]
		if !cached {
			if tags, err = tx.ExportSubscriptionTags(ctx, *row.SubscriptionID); err != nil {
				return nil, err
			}
			owned[*row.SubscriptionID] = tags
		}
		names := make([]string, 0, len(tags))
		for _, tag := range tags {
			names = append(names, tag.Name)
		}
		values := []xlsxexport.Value{xlsxexport.OptionalStr(row.LastName), xlsxexport.OptionalStr(row.FirstName),
			xlsxexport.StrValue(printingDate(*row.BornDate)), xlsxexport.OptionalStr(row.Email), xlsxexport.OptionalStr(row.Phone),
			xlsxexport.OptionalStr(row.CourseTitle), xlsxexport.StrValue(printingDate(row.CreationDate.UTC())),
			xlsxexport.StrValue(strings.Join(names, ", "))}
		if len(lookup) > 0 {
			carnet := strings.Join(lookup[row.ID], ", ")
			if carnet == "" {
				carnet = "-"
			}
			values = append(values, xlsxexport.StrValue(carnet))
		}
		for column, value := range values {
			cells[column] = append(cells[column], value)
		}
	}
	if format == "pdf" {
		// generate_pdf_from_data(zip(*rows), columns, title=...): the
		// document carries the title, the filename a fixed one; the per-tag
		// columns belong to the workbook.
		document, err := pdfexport.DataTable{Columns: columns, Rows: pdfexport.RowsOf(cells),
			Title: title + " (giorno stampa " + printingDate(today) + ")"}.Build(today)
		switch {
		case errors.Is(err, pdfexport.ErrNotPorted):
			return nil, ErrCourseSubscriptionExportUnsupported
		case err != nil:
			return nil, domain.ErrCourseSubscriptionServer // an exception of reportlab
		}
		denomination, err := tx.AssociationDenomination(ctx, export.query.AssociationID)
		if err != nil {
			return nil, err
		}
		name := "None"
		if denomination != nil {
			name = *denomination
		}
		return map[string]any{"file": base64.StdEncoding.EncodeToString(document),
			"filename": fmt.Sprintf("[%s] Iscrizioni corsi filtrate - %s.pdf", printingDate(today), name)}, nil
	}
	association, err := tx.ExportAssociationTags(ctx, export.query.AssociationID)
	if err != nil {
		return nil, err
	}
	for _, tag := range association {
		columns = append(columns, tag.Name)
		column := make([]xlsxexport.Value, 0, len(rows))
		for _, row := range rows {
			assigned := ""
			for _, own := range owned[*row.SubscriptionID] {
				if own.ID == tag.ID {
					assigned = "assegnato"
					break
				}
			}
			column = append(column, xlsxexport.StrValue(assigned))
		}
		cells = append(cells, column)
	}
	workbook, err := xlsxexport.PandasOpenpyxl(cells, columns, s.now())
	switch {
	case errors.Is(err, xlsxexport.ErrNotPorted):
		return nil, ErrCourseSubscriptionExportUnsupported
	case errors.Is(err, xlsxexport.ErrDjango):
		return nil, domain.ErrCourseSubscriptionServer // get_excel_base64 returned None: generate_xlsx_from_data raises
	case err != nil:
		return nil, err
	}
	denomination, err := tx.AssociationDenomination(ctx, export.query.AssociationID)
	if err != nil {
		return nil, err
	}
	name := "None"
	if denomination != nil {
		name = *denomination
	}
	return map[string]any{"file": base64.StdEncoding.EncodeToString(workbook),
		"filename": fmt.Sprintf("[%s] %s - %s.xlsx", printingDate(today), title, name)}, nil
}
