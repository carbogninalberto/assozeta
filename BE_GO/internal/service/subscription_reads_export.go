package service

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pdfexport"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/xlsxexport"
)

// SubscriptionReadsExport is a POST subscription/list without m: the handler
// reads request.data and calls Export with it (current_view_subscriptions).
type SubscriptionReadsExport struct {
	filter      domain.SubscriptionReadsFilter
	group       *string
	association string
	title       string
	lazy        bool
}

func (e *SubscriptionReadsExport) Error() string { return "subscription list export" }

// subscriptionReadsStatusLang is Subscription.get_status_lang(); another
// status is a KeyError.
var subscriptionReadsStatusLang = map[int]string{1: "non firmato", 2: "in attesa", 3: "rifiutato", 4: "accettato", 5: "archiviato"}

// Export is current_view_subscriptions(sport_association, request.data,
// subscriptions, title=printing_title): data['format'] must be 'pdf' or
// 'excel'; 'excel' is generate_xlsx_from_data of eight columns over the whole
// filtered, sorted queryset and the response is {'file': <base64>,
// 'filename': '[<today>] <title> - <denomination>.xlsx'}; 'pdf' is
// generate_pdf_from_data of the same columns as rows, titled '<title> (giorno
// stampa <today>)' and named '[<today>] Iscrizioni filtrate -
// <denomination>.pdf'.
// Every exception is the view's 500.
func (s *SubscriptionReads) Export(ctx context.Context, export *SubscriptionReadsExport, data any) (map[string]any, error) {
	format, err := dto.PythonIndex(data, "format")
	if err != nil {
		return nil, domain.ErrSubscriptionReadsServer // KeyError or TypeError
	}
	switch format {
	case "excel", "pdf":
	default:
		return nil, domain.ErrSubscriptionReadsServer // Exception('Format not valid')
	}
	now := s.now().UTC()
	if export.lazy {
		// The ordering is compiled when the rows are iterated (here).
		return nil, domain.ErrSubscriptionReadsNotPorted
	}
	ids, err := s.store.Subscriptions(ctx, export.filter, -1, 0)
	if err != nil {
		return nil, err
	}
	rows, err := s.rows(ctx, ids, export.group)
	if err != nil {
		return nil, err
	}
	distinct, seen := []string{}, map[string]bool{}
	for _, id := range ids {
		if !seen[id] {
			seen[id] = true
			distinct = append(distinct, id)
		}
	}
	prefetched, err := s.store.ExportTags(ctx, distinct)
	if err != nil {
		return nil, err
	}
	tags := map[string][]string{}
	for _, tag := range prefetched {
		tags[tag.SubscriptionID] = append(tags[tag.SubscriptionID], tag.Name)
	}
	// The view's rows put the creation date under 'Certificato' and the
	// certificate under 'Data creazione'.
	columns := []string{"Tesserato", "Email", "Stato", "Anno fiscale", "Certificato", "Data creazione", "Utente", "Tag"}
	cells := make([][]xlsxexport.Value, len(columns))
	today := s.today()
	for _, row := range rows {
		if row.Associate == nil {
			return nil, domain.ErrSubscriptionReadsServer // s.associate is None: AttributeError
		}
		status, ok := subscriptionReadsStatusLang[row.StatusFlag]
		if !ok {
			return nil, domain.ErrSubscriptionReadsServer
		}
		// current_year(): 'corrente' without both dates or within them.
		year := "corrente"
		if row.StartDate != nil && row.EndDate != nil && !dto.SubscriptionReadsCurrent(row, now) {
			year = "successivo"
			if row.EndDate.Before(today) {
				year = "precedente"
			}
		}
		medical := "-"
		if row.MedicalID != nil {
			medical = "presente"
		}
		// get_full_name(): ('%s %s' % (first_name, last_name)).strip().
		name := dto.PythonStrip(subscriptionReadsPythonStr(row.Associate.FirstName) + " " + subscriptionReadsPythonStr(row.Associate.LastName))
		for column, value := range []xlsxexport.Value{xlsxexport.StrValue(name), xlsxexport.OptionalStr(row.Associate.Email),
			xlsxexport.StrValue(status), xlsxexport.StrValue(year), xlsxexport.StrValue(printingDate(row.CreationDate.UTC())),
			xlsxexport.StrValue(medical), xlsxexport.StrValue(row.UserUsername), xlsxexport.StrValue(strings.Join(tags[row.ID], ", "))} {
			cells[column] = append(cells[column], value)
		}
	}
	if format == "pdf" {
		// generate_pdf_from_data(zip(*rows), columns, title=...): the
		// filename keeps the default title, the document the view's.
		document, err := pdfexport.DataTable{Columns: columns, Rows: pdfexport.RowsOf(cells),
			Title: export.title + " (giorno stampa " + printingDate(now) + ")"}.Build(now)
		switch {
		case errors.Is(err, pdfexport.ErrNotPorted):
			return nil, domain.ErrSubscriptionReadsNotPorted
		case err != nil:
			return nil, domain.ErrSubscriptionReadsServer // an exception of reportlab
		}
		denomination, err := s.store.AssociationDenomination(ctx, export.association)
		if err != nil {
			return nil, err
		}
		return map[string]any{"file": base64.StdEncoding.EncodeToString(document),
			"filename": fmt.Sprintf("[%s] Iscrizioni filtrate - %s.pdf", printingDate(now), subscriptionReadsPythonStr(denomination))}, nil
	}
	workbook, err := xlsxexport.PandasOpenpyxl(cells, columns, s.now())
	switch {
	case errors.Is(err, xlsxexport.ErrNotPorted):
		return nil, domain.ErrSubscriptionReadsNotPorted
	case errors.Is(err, xlsxexport.ErrDjango):
		return nil, domain.ErrSubscriptionReadsServer // get_excel_base64 returned None: generate_xlsx_from_data raises
	case err != nil:
		return nil, err
	}
	denomination, err := s.store.AssociationDenomination(ctx, export.association)
	if err != nil {
		return nil, err
	}
	return map[string]any{"file": base64.StdEncoding.EncodeToString(workbook),
		"filename": fmt.Sprintf("[%s] %s - %s.xlsx", printingDate(now), export.title, subscriptionReadsPythonStr(denomination))}, nil
}

// subscriptionReadsPythonStr is str() of a nullable text column.
func subscriptionReadsPythonStr(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}
