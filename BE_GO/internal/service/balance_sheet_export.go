package service

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"math"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/xlsxexport"
)

// balanceSheetExport is export_balance_sheet's outcome: the response body,
// or failed when the function raises (after the sheet is saved).
type balanceSheetExport struct {
	body   map[string]any
	failed bool
}

// balanceSheetComma is f"{value:.2f}".replace(".", ",").
func balanceSheetComma(value float64) string {
	return strings.ReplaceAll(strconv.FormatFloat(value, 'f', 2, 64), ".", ",")
}

// export is export_balance_sheet(balance_sheet, 'excel') over the data the
// view is about to save: generate_xlsx_from_data of Descrizione,
// Istituzionale (€), Commerciale (€), Totale (€) and the filename
// '[<today>] Bilancio <year> - <denomination>.xlsx'. An error is a refusal
// (an input Go does not reproduce); failed is a Python exception.
func (r *sheetRun) export(ctx context.Context, data *pyDict, year int) (*balanceSheetExport, error) {
	failed := &balanceSheetExport{failed: true}
	today := r.s.now().UTC()
	rows := make([][]xlsxexport.Value, 4)
	add := func(description xlsxexport.Value, institutional, commercial, total string) {
		for column, value := range []xlsxexport.Value{description, xlsxexport.StrValue(institutional), xlsxexport.StrValue(commercial),
			xlsxexport.StrValue(total)} {
			rows[column] = append(rows[column], value)
		}
	}
	text := func(description string) { add(xlsxexport.StrValue(description), "", "", "") }
	var sums [2][2]float64
	for index, section := range []struct{ heading, title, direction, key, footer string }{
		{"A) ENTRATE", "Entrate Generali", "incoming", "generalIncome", "Totale entrate"},
		{"B) USCITE", "Spese generali", "outgoing", "generalExpenses", "Totale uscite"}} {
		text(section.heading)
		text(section.title)
		group, err := pyIndex(data, section.direction)
		if err != nil {
			return failed, nil
		}
		list, err := pyIndex(group, section.key)
		if err != nil {
			return failed, nil
		}
		items, err := pyIterate(list)
		if err != nil {
			return failed, nil
		}
		for _, item := range items {
			var amounts [2]float64
			for i, key := range []string{"institutional", "commercial"} {
				raw, err := pyIndex(item, key)
				if err != nil {
					return failed, nil
				}
				if amounts[i], err = pyFloat(raw); err != nil {
					return failed, nil
				}
				if math.IsNaN(amounts[i]) || math.IsInf(amounts[i], 0) {
					return nil, ErrBalanceSheetExportNotPorted
				}
			}
			raw, err := pyIndex(item, "description")
			if err != nil {
				return failed, nil
			}
			description := xlsxexport.NoneValue()
			switch value := raw.(type) {
			case nil:
			case string:
				description = xlsxexport.StrValue(value)
			default:
				return nil, ErrBalanceSheetExportNotPorted // a number, list or dict cell
			}
			add(description, balanceSheetComma(amounts[0]), balanceSheetComma(amounts[1]), balanceSheetComma(amounts[0]+amounts[1]))
			sums[index][0] += amounts[0]
			sums[index][1] += amounts[1]
		}
		add(xlsxexport.StrValue(section.footer), balanceSheetComma(sums[index][0]), balanceSheetComma(sums[index][1]),
			balanceSheetComma(sums[index][0]+sums[index][1]))
		text("")
	}
	income, outgoing := sums[0][0]+sums[0][1], sums[1][0]+sums[1][1]
	add(xlsxexport.StrValue(""), "ENTRATE", "USCITE", "BILANCIO FINALE")
	add(xlsxexport.StrValue("C) RENDICONTO DELLA GESTIONE"), balanceSheetComma(income), "-"+balanceSheetComma(outgoing),
		balanceSheetComma(income-sums[1][0]-sums[1][1]))
	text("")
	text("D) LIQUIDITÀ")
	for _, line := range [][2]string{{"Banca", "bank"}, {"Cassa", "cash"}, {"Altro", "other"}, {"Totale", "total"}} {
		raw, err := pyIndex(data, line[1])
		if err != nil {
			return failed, nil
		}
		amount, err := pyFloat(raw)
		if err != nil {
			return failed, nil
		}
		if math.IsNaN(amount) || math.IsInf(amount, 0) {
			return nil, ErrBalanceSheetExportNotPorted
		}
		add(xlsxexport.StrValue(line[0]), "", "", balanceSheetComma(amount))
	}
	// balance_at_date: strptime('<start day>/<start month>/<year>', '%d/%m/%Y'),
	// then .replace(year=year + 1); both raise ValueError on an invalid date.
	day, month, err := r.tx.StartDate(ctx, r.identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	if year < 1000 || year > 9999 || month < 1 || month > 12 || day < 1 {
		return failed, nil
	}
	for _, y := range []int{year, year + 1} {
		if date := time.Date(y, time.Month(month), int(day), 0, 0, 0, 0, time.UTC); date.Month() != time.Month(month) || y > 9999 {
			return failed, nil
		}
	}
	profile, err := r.profile(ctx)
	if err != nil {
		return nil, err
	}
	workbook, err := xlsxexport.PandasOpenpyxl(rows, []string{"Descrizione", "Istituzionale (€)", "Commerciale (€)", "Totale (€)"}, r.s.now())
	switch {
	case errors.Is(err, xlsxexport.ErrNotPorted):
		return nil, ErrBalanceSheetExportNotPorted
	case errors.Is(err, xlsxexport.ErrDjango):
		return failed, nil // get_excel_base64 returned None: generate_xlsx_from_data raises
	case err != nil:
		return nil, err
	}
	return &balanceSheetExport{body: map[string]any{"file": base64.StdEncoding.EncodeToString(workbook),
		"filename": fmt.Sprintf("[%s] Bilancio %d - %s.xlsx", printingDate(today), year, profile.Denomination)}}, nil
}
